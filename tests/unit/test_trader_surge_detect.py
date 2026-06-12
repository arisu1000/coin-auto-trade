"""
거래대금 급등 감지(_detect_surge_markets) 단위 테스트

배경: 24h 누적 거래대금 순위(top_n)는 후행 지표라 급등 초기 종목을 놓친다.
(실사례: 2026-06-12 KRW-WAVES — 15:30 KST 거래량 폭발 후 4시간 19분 뒤에야
top 15 진입, 결국 고점 부근 505원에 매수)

검증 항목:
- 절대 임계값 + 평소 페이스 배수 조건을 모두 충족하면 감시 목록에 추가
- 절대 임계값 미달 시 미감지
- 평소 거래대금이 큰 종목(BTC 등)은 배수 조건으로 오탐 방지
- 첫 호출은 기준 스냅샷만 기록하고 감지하지 않음
- TTL 만료 시 감시 목록에서 제거
- 급등 지속 시 TTL 연장 (중복 알림 없음)
- 매수 제외/투자유의 마켓은 추가하지 않음
- 급등 마켓은 _active_markets(감시)와 _buy_target_markets(매수 허용)에 포함
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from src.core.trader import Trader

MARKET = "KRW-WAVES"


def _settings(**overrides) -> MagicMock:
    s = MagicMock()
    s.target_markets_top_n = 15
    s.surge_detect_enabled = True
    s.surge_threshold_krw = 300_000_000.0
    s.surge_multiplier = 10.0
    s.surge_ttl_minutes = 240
    for key, value in overrides.items():
        setattr(s, key, value)
    return s


def _trader(tickers: list[dict], **settings_overrides) -> Trader:
    t = Trader(_settings(**settings_overrides))
    t._fetch_all_tickers = AsyncMock(return_value=tickers)
    t._bot = None
    return t


def _ticker(market: str, acc: float) -> dict:
    return {"market": market, "acc_trade_price_24h": acc}


async def _baseline(t: Trader, tickers: list[dict], minutes_ago: float = 5.0) -> None:
    """기준 스냅샷을 기록하고 시각을 과거로 되돌린다 (경과 시간 시뮬레이션)."""
    t._fetch_all_tickers.return_value = tickers
    await t._detect_surge_markets()
    t._acc_trade_snapshot_at = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)


class TestSurgeDetection:
    async def test_detects_surge_meeting_both_conditions(self):
        # WAVES 실사례 축소판: 평소 24h 5억원 → 5분 만에 +10억원
        t = _trader([_ticker(MARKET, 500_000_000)])
        await _baseline(t, [_ticker(MARKET, 500_000_000)])

        t._fetch_all_tickers.return_value = [_ticker(MARKET, 1_500_000_000)]
        await t._detect_surge_markets()

        assert MARKET in t._active_surge_markets

    async def test_ignores_delta_below_absolute_threshold(self):
        # +1억원 증가: 배수 조건은 충족하지만 절대 임계값(3억) 미달
        t = _trader([])
        await _baseline(t, [_ticker(MARKET, 100_000_000)])

        t._fetch_all_tickers.return_value = [_ticker(MARKET, 200_000_000)]
        await t._detect_surge_markets()

        assert MARKET not in t._active_surge_markets

    async def test_ignores_large_cap_normal_volume(self):
        # BTC: 평소 24h 5,000억원 → 5분당 평소 페이스 ~1.7억원.
        # +5억원 증가는 절대 임계값은 넘지만 평소 페이스의 10배 미만 → 미감지
        t = _trader([])
        await _baseline(t, [_ticker("KRW-BTC", 500_000_000_000)])

        t._fetch_all_tickers.return_value = [_ticker("KRW-BTC", 500_500_000_000)]
        await t._detect_surge_markets()

        assert "KRW-BTC" not in t._active_surge_markets

    async def test_first_call_only_records_baseline(self):
        t = _trader([_ticker(MARKET, 99_000_000_000)])
        await t._detect_surge_markets()

        assert t._active_surge_markets == set()
        assert t._acc_trade_snapshot[MARKET] == 99_000_000_000

    async def test_skips_excluded_and_warned_markets(self):
        t = _trader([])
        t._excluded_markets["KRW-EXCL"] = "수동 제외"
        t._warned_markets.add("KRW-WARN")
        before = [_ticker("KRW-EXCL", 500_000_000), _ticker("KRW-WARN", 500_000_000)]
        await _baseline(t, before)

        t._fetch_all_tickers.return_value = [
            _ticker("KRW-EXCL", 1_500_000_000), _ticker("KRW-WARN", 1_500_000_000)
        ]
        await t._detect_surge_markets()

        assert t._active_surge_markets == set()


class TestSurgeLifecycle:
    async def test_expired_market_removed(self):
        t = _trader([_ticker(MARKET, 500_000_000)])
        t._surge_markets[MARKET] = datetime.now(timezone.utc) - timedelta(minutes=1)

        assert MARKET not in t._active_surge_markets
        await t._detect_surge_markets()
        assert MARKET not in t._surge_markets

    async def test_ongoing_surge_extends_ttl_without_duplicate_alert(self):
        t = _trader([])
        t._bot = MagicMock()
        t._bot.send_alert = AsyncMock()
        old_expiry = datetime.now(timezone.utc) + timedelta(minutes=30)
        t._surge_markets[MARKET] = old_expiry
        await _baseline(t, [_ticker(MARKET, 500_000_000)])

        t._fetch_all_tickers.return_value = [_ticker(MARKET, 1_500_000_000)]
        await t._detect_surge_markets()

        assert t._surge_markets[MARKET] > old_expiry
        t._bot.send_alert.assert_not_awaited()

    async def test_new_surge_sends_alert(self):
        t = _trader([])
        t._bot = MagicMock()
        t._bot.send_alert = AsyncMock()
        await _baseline(t, [_ticker(MARKET, 500_000_000)])

        t._fetch_all_tickers.return_value = [_ticker(MARKET, 1_500_000_000)]
        await t._detect_surge_markets()

        t._bot.send_alert.assert_awaited_once()
        assert MARKET in t._bot.send_alert.call_args[0][0]

    async def test_fetch_failure_keeps_state_intact(self):
        t = _trader([])
        await _baseline(t, [_ticker(MARKET, 500_000_000)])
        snapshot_before = dict(t._acc_trade_snapshot)

        t._fetch_all_tickers.side_effect = RuntimeError("API down")
        await t._detect_surge_markets()  # 예외 전파 없이 동작해야 한다

        assert t._acc_trade_snapshot == snapshot_before


class TestSurgeMarketVisibility:
    async def test_surge_market_is_watched_and_buyable(self):
        t = _trader([])
        t._dynamic_markets = ["KRW-BTC", "KRW-ETH"]
        t._surge_markets[MARKET] = datetime.now(timezone.utc) + timedelta(hours=1)

        assert MARKET in t._active_markets
        assert MARKET in t._buy_target_markets

    async def test_expired_surge_market_not_buyable(self):
        t = _trader([])
        t._dynamic_markets = ["KRW-BTC"]
        t._surge_markets[MARKET] = datetime.now(timezone.utc) - timedelta(minutes=1)

        assert MARKET not in t._active_markets
        assert MARKET not in t._buy_target_markets
