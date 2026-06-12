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
- 빈 티커 응답은 기준 스냅샷을 덮어쓰지 않음 (감지 무력화 방지)
- TTL 만료/경고·제외 지정 시 감시 목록에서 제거
- 급등 지속 시 TTL 연장 (중복 알림 없음)
- 매수 제외/투자유의/기준(base) 마켓은 추가하지 않음
- 동시 감시 수 상한(surge_max_markets): Δ가 큰 순서로만 채움
- 급등 마켓은 _active_markets(감시)와 _buy_target_markets(매수 허용)에 포함
- 비활성화 또는 고정 마켓 모드(top_n=0)면 기존 감지 종목도 즉시 매수 불허
- _markets_held: locked 잔량도 보유로 간주 (포지션 상태 오삭제 방지)
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import Settings
from src.core.trader import Trader
from src.exchange.models import Balance

MARKET = "KRW-WAVES"


def _settings(**overrides) -> MagicMock:
    s = MagicMock()
    s.target_markets_top_n = 15
    s.surge_detect_enabled = True
    s.surge_threshold_krw = 300_000_000.0
    s.surge_multiplier = 10.0
    s.surge_ttl_minutes = 240
    s.surge_max_markets = 5
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

    async def test_skips_markets_already_in_base_set(self):
        # 이미 top_n 정규 감시 대상이면 surge로 추적하지 않는다
        # (top_n 이탈 '제거' 알림과 실제 매수 허용 상태가 모순되지 않도록)
        t = _trader([])
        t._dynamic_markets = [MARKET]
        await _baseline(t, [_ticker(MARKET, 500_000_000)])

        t._fetch_all_tickers.return_value = [_ticker(MARKET, 1_500_000_000)]
        await t._detect_surge_markets()

        assert MARKET not in t._surge_markets

    async def test_empty_ticker_response_preserves_baseline(self):
        # 빈 응답이 기준 스냅샷을 지우면 다음 틱이 재기준선만 기록하게 되어
        # 그 사이 시작된 급등을 놓친다 — 스냅샷은 보존되어야 한다
        t = _trader([])
        await _baseline(t, [_ticker(MARKET, 500_000_000)])
        snapshot_before = dict(t._acc_trade_snapshot)

        t._fetch_all_tickers.return_value = []
        await t._detect_surge_markets()

        assert t._acc_trade_snapshot == snapshot_before

        # 보존된 기준선으로 급등 감지가 정상 동작해야 한다
        t._fetch_all_tickers.return_value = [_ticker(MARKET, 1_500_000_000)]
        await t._detect_surge_markets()
        assert MARKET in t._active_surge_markets

    async def test_cap_limits_concurrent_surge_markets_by_delta(self):
        # 상한 2: Δ가 큰 상위 2개만 추가되고 나머지는 건너뛴다
        t = _trader([], surge_max_markets=2)
        before = [_ticker(f"KRW-C{i}", 500_000_000) for i in range(4)]
        await _baseline(t, before)

        deltas = [1_000_000_000, 3_000_000_000, 2_000_000_000, 900_000_000]
        t._fetch_all_tickers.return_value = [
            _ticker(f"KRW-C{i}", 500_000_000 + d) for i, d in enumerate(deltas)
        ]
        await t._detect_surge_markets()

        assert t._active_surge_markets == {"KRW-C1", "KRW-C2"}

    async def test_cap_counts_existing_surge_markets(self):
        t = _trader([], surge_max_markets=1)
        t._surge_markets["KRW-HELD"] = datetime.now(timezone.utc) + timedelta(hours=1)
        await _baseline(t, [_ticker(MARKET, 500_000_000)])

        t._fetch_all_tickers.return_value = [_ticker(MARKET, 1_500_000_000)]
        await t._detect_surge_markets()

        assert MARKET not in t._surge_markets  # 슬롯 없음


class TestSurgeLifecycle:
    async def test_expired_market_removed(self):
        t = _trader([_ticker(MARKET, 500_000_000)])
        t._surge_markets[MARKET] = datetime.now(timezone.utc) - timedelta(minutes=1)

        assert MARKET not in t._active_surge_markets
        await t._detect_surge_markets()
        assert MARKET not in t._surge_markets

    async def test_warned_market_pruned_from_surge_set(self):
        # 감지 후 투자유의 지정된 종목은 TTL을 기다리지 않고 즉시 감시에서 제외
        t = _trader([_ticker(MARKET, 500_000_000)])
        t._surge_markets[MARKET] = datetime.now(timezone.utc) + timedelta(hours=1)
        t._warned_markets.add(MARKET)

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

    async def test_disabling_flag_immediately_revokes_buy_permission(self):
        # 핫 리로드로 비활성화하면 이미 감지된 종목도 즉시 매수 대상에서 제외
        t = _trader([], surge_detect_enabled=False)
        t._dynamic_markets = ["KRW-BTC"]
        t._surge_markets[MARKET] = datetime.now(timezone.utc) + timedelta(hours=1)

        assert MARKET not in t._buy_target_markets
        assert MARKET not in t._active_markets

    async def test_fixed_market_mode_ignores_surge_set(self):
        # top_n=0(고정 화이트리스트 모드)에서는 급등 감지가 화이트리스트를 우회할 수 없다
        t = _trader([], target_markets_top_n=0)
        t._settings.markets_list = ["KRW-BTC", "KRW-ETH"]
        t._surge_markets[MARKET] = datetime.now(timezone.utc) + timedelta(hours=1)

        assert not t._surge_detect_active
        assert MARKET not in t._buy_target_markets
        assert t._buy_target_markets == {"KRW-BTC", "KRW-ETH"}


class TestMarketsHeld:
    def test_locked_balance_counts_as_held(self):
        # 전량이 미체결 주문에 잠겨 available==0이어도 보유로 판정해야
        # _forget_position이 살아있는 포지션 상태를 지우지 않는다
        balances = [
            Balance(currency="KRW", balance=1_000_000, locked=0, avg_buy_price=0),
            Balance(currency="WAVES", balance=0, locked=99.0, avg_buy_price=505),
            Balance(currency="BTC", balance=0.01, locked=0, avg_buy_price=1e8),
            Balance(currency="XRP", balance=0, locked=0, avg_buy_price=0),
        ]
        held = Trader._markets_held(balances)
        assert held == {"KRW-WAVES", "KRW-BTC"}


class TestSurgeSettingsValidation:
    _REQUIRED = dict(
        upbit_access_key="k", upbit_secret_key="s",
        openai_api_key="o", telegram_bot_token="t", telegram_chat_id="1",
    )

    def test_zero_allowed_when_disabled(self):
        # 코드베이스의 "0 = 비활성" 관례를 따라 0을 넣어도 부팅이 실패하면 안 된다
        s = Settings(
            _env_file=None, **self._REQUIRED,
            surge_detect_enabled=False, surge_multiplier=0,
        )
        assert s.surge_multiplier == 0

    def test_zero_rejected_when_enabled(self):
        with pytest.raises(ValueError, match="surge_multiplier"):
            Settings(
                _env_file=None, **self._REQUIRED,
                surge_detect_enabled=True, surge_multiplier=0,
            )
