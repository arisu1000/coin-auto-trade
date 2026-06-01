"""
Trader 매매 결정 헬퍼 단위 테스트 (_strategy_loop 리팩토링으로 분리된 로직)

검증 항목:
- _apply_buy_blocks: 제외/거래지원종료 마켓의 BUY 차단, 그 외는 통과
- _reconcile_pyramid_exit: 실제 진입가 기준 손절·트레일 재검증이 시뮬레이션
  결과보다 우선하고, 갱신된 최고가를 DB에 저장
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from src.core.trader import Trader


class _Strat:
    name = "pyramid_breakout"

    def __init__(self, stop_pct: float = 10.0, trail_pct: float = 8.0) -> None:
        self.stop_pct = stop_pct
        self.trail_pct = trail_pct


def _trader() -> Trader:
    t = Trader(MagicMock())
    t._pyramid_repo = AsyncMock()
    return t


MARKET = "KRW-AERGO"
CANDLE_TS = datetime(2026, 6, 1, 11, 0, 0)


# ── _apply_buy_blocks ───────────────────────────────────────────────────────

class TestApplyBuyBlocks:
    def test_excluded_market_blocks_buy(self):
        t = _trader()
        t._excluded_markets = {MARKET: "설정값"}
        assert t._apply_buy_blocks(MARKET, "BUY") == "HOLD"

    def test_warned_market_blocks_buy(self):
        t = _trader()
        t._warned_markets = {MARKET}
        assert t._apply_buy_blocks(MARKET, "BUY") == "HOLD"

    def test_clean_market_allows_buy(self):
        t = _trader()
        assert t._apply_buy_blocks(MARKET, "BUY") == "BUY"

    def test_sell_passes_through_even_if_excluded(self):
        """차단은 BUY에만 적용 — SELL/HOLD는 그대로 통과한다."""
        t = _trader()
        t._excluded_markets = {MARKET: "설정값"}
        t._warned_markets = {MARKET}
        assert t._apply_buy_blocks(MARKET, "SELL") == "SELL"
        assert t._apply_buy_blocks(MARKET, "HOLD") == "HOLD"


# ── _reconcile_pyramid_exit ─────────────────────────────────────────────────

class TestReconcilePyramidExit:
    def _setup(self, t: Trader, entry_price: float, highest: float) -> None:
        t._pyramid_state = {MARKET: {"entry_price": entry_price, "add_count": 0}}
        t._position_highest = {MARKET: highest}
        t._position_entry_ts = {}  # entry_ts 없음 → intraday 사용(오래된 포지션)

    async def test_real_stop_loss_overrides_to_sell(self):
        t = _trader()
        self._setup(t, entry_price=100.0, highest=100.0)
        # 현재가 85 → 진입가 대비 -15%, stop 10% 이탈
        result = await t._reconcile_pyramid_exit(
            MARKET, decision="HOLD", current_price=85.0, strategy=_Strat(stop_pct=10.0),
            candle_high=100.0, candle_low=85.0, candle_ts=CANDLE_TS,
        )
        assert result == "SELL"

    async def test_simulation_sell_downgraded_to_hold_when_position_healthy(self):
        """시뮬레이션이 SELL이어도 실제 상태 기준 미충족이면 HOLD로 강등된다."""
        t = _trader()
        self._setup(t, entry_price=100.0, highest=100.0)
        result = await t._reconcile_pyramid_exit(
            MARKET, decision="SELL", current_price=105.0, strategy=_Strat(),
            candle_high=105.0, candle_low=104.0, candle_ts=CANDLE_TS,
        )
        assert result == "HOLD"
        # 최고가가 100 → 105로 갱신되어 DB에 저장됐는지 확인
        _, kwargs = t._pyramid_repo.save.call_args
        assert kwargs["highest_price"] == 105.0
        assert t._position_highest[MARKET] == 105.0

    async def test_buy_decision_passes_through_when_no_exit(self):
        t = _trader()
        self._setup(t, entry_price=100.0, highest=100.0)
        result = await t._reconcile_pyramid_exit(
            MARKET, decision="BUY", current_price=105.0, strategy=_Strat(),
            candle_high=105.0, candle_low=104.0, candle_ts=CANDLE_TS,
        )
        assert result == "BUY"

    async def test_no_save_when_highest_unchanged(self):
        """최고가가 갱신되지 않으면 DB 저장을 호출하지 않는다."""
        t = _trader()
        self._setup(t, entry_price=100.0, highest=110.0)
        await t._reconcile_pyramid_exit(
            MARKET, decision="HOLD", current_price=105.0, strategy=_Strat(),
            candle_high=108.0, candle_low=104.0, candle_ts=CANDLE_TS,
        )
        t._pyramid_repo.save.assert_not_awaited()
