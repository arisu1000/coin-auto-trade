"""
Trader._check_position_exit 단위 테스트

회귀 대상 버그:
- 피라미딩 진입 직후, 진입 봉(또는 진입 이전 가격을 포함한 일봉/분봉)의
  고가가 최고가를 부풀리고 같은 봉의 (진입 전) 저가가 트레일링 스탑을
  트리거해, 매수 1봉 만에 같은 가격으로 즉시 청산되던 휩쏘.

검증 항목:
- 진입 봉에서는 intraday 고저가를 배제하고 현재가만 사용 → 즉시 청산 안 됨
- 진입 이후 형성된 봉에서는 intraday 고저가 정상 사용 (트레일·손절 동작)
- entry_ts 미존재(재시작·수동 등록) 시 기존 동작 유지
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

from src.core.trader import Trader


class _Strat:
    """손절/트레일 비율만 노출하는 전략 스텁."""

    name = "pyramid_breakout"

    def __init__(self, stop_pct: float = 10.0, trail_pct: float = 8.0) -> None:
        self.stop_pct = stop_pct
        self.trail_pct = trail_pct


def _trader() -> Trader:
    t = Trader(MagicMock())
    return t


MARKET = "KRW-AERGO"
ENTRY = datetime(2026, 6, 1, 10, 14, 0)  # naive UTC


def _setup(t: Trader, entry_price: float, highest: float | None = None,
           entry_ts: datetime | None = ENTRY) -> None:
    t._pyramid_state = {MARKET: {"entry_price": entry_price, "add_count": 0}}
    t._position_highest = {MARKET: highest if highest is not None else entry_price}
    t._position_entry_ts = {MARKET: entry_ts} if entry_ts is not None else {}


# ── 회귀: 진입 봉 휩쏘 방지 ──────────────────────────────────────────────────

class TestEntryBarWhipsaw:
    def test_entry_bar_does_not_trigger_trail(self):
        """진입 봉(일봉)의 고가가 최고가를 부풀리고 진입 전 저가가 트레일을
        트리거하던 버그 — 이제 HOLD여야 한다."""
        t = _trader()
        _setup(t, entry_price=87.0, highest=87.0)
        # 진입 봉(일봉) 시작 시각이 진입 시각보다 앞섬 → intraday 배제 대상
        bar_ts = ENTRY - timedelta(hours=10)  # 당일 00:00 일봉
        decision = t._check_position_exit(
            MARKET, current_price=87.0, strategy=_Strat(trail_pct=8.0),
            candle_high=95.0,   # 진입 전 당일 고점
            candle_low=80.0,    # 진입 전 당일 저점 (트레일 트리거 유발하던 값)
            candle_ts=bar_ts,
        )
        assert decision == "HOLD"
        # 최고가도 진입 전 고가(95)로 부풀지 않아야 한다
        assert t._position_highest[MARKET] == 87.0

    def test_entry_bar_real_stop_loss_still_uses_current_price(self):
        """진입 봉이라도 현재가가 손절선을 이탈하면 손절은 동작해야 한다."""
        t = _trader()
        _setup(t, entry_price=100.0, highest=100.0)
        bar_ts = ENTRY - timedelta(hours=10)
        decision = t._check_position_exit(
            MARKET, current_price=85.0,  # -15%, stop 10% 이탈
            strategy=_Strat(stop_pct=10.0),
            candle_high=120.0, candle_low=70.0, candle_ts=bar_ts,
        )
        assert decision == "SELL"


# ── 진입 이후 봉: intraday 정상 동작 ────────────────────────────────────────

class TestPostEntryBar:
    def test_post_entry_trail_triggers_on_candle_low(self):
        """진입 이후 형성된 봉에서는 intraday 저가로 트레일이 동작한다."""
        t = _trader()
        # 진입가 87, 이후 100까지 상승해 최고가 100
        _setup(t, entry_price=87.0, highest=100.0)
        bar_ts = ENTRY + timedelta(minutes=20)  # 진입 후 봉
        # trail 8% → 100*0.92=92, 봉 저가 90 <= 92 → SELL
        decision = t._check_position_exit(
            MARKET, current_price=93.0, strategy=_Strat(trail_pct=8.0),
            candle_high=100.0, candle_low=90.0, candle_ts=bar_ts,
        )
        assert decision == "SELL"

    def test_post_entry_high_updates_highest(self):
        """진입 이후 봉의 고가는 최고가를 정상 갱신한다."""
        t = _trader()
        _setup(t, entry_price=100.0, highest=100.0)
        bar_ts = ENTRY + timedelta(minutes=20)
        t._check_position_exit(
            MARKET, current_price=110.0, strategy=_Strat(),
            candle_high=120.0, candle_low=108.0, candle_ts=bar_ts,
        )
        assert t._position_highest[MARKET] == 120.0


# ── entry_ts 미존재(재시작·수동 등록) ───────────────────────────────────────

class TestNoEntryTimestamp:
    def test_without_entry_ts_uses_intraday(self):
        """entry_ts가 없으면 오래된 포지션으로 보고 intraday 값을 그대로 사용한다."""
        t = _trader()
        _setup(t, entry_price=87.0, highest=100.0, entry_ts=None)
        decision = t._check_position_exit(
            MARKET, current_price=93.0, strategy=_Strat(trail_pct=8.0),
            candle_high=100.0, candle_low=90.0,
            candle_ts=ENTRY,  # entry_ts 없으므로 무시됨
        )
        assert decision == "SELL"
