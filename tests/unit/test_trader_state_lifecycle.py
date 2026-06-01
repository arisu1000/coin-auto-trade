"""
Trader 포지션 상태 라이프사이클 단위 테스트

검증 항목 (코드리뷰 후속 #1, #3):
- _stamp_entry_ts: 진입 시각을 naive UTC로 기록하고 ISO 문자열 반환
- _forget_position: 3개 상태 딕셔너리 + DB 레코드 일괄 정리
- set_pyramid_state(/pyramid_set): 수동 등록 시에도 entry_ts 기록·영속화
- sync_pyramid_state(/sync): 동기화로 발견한 포지션에도 entry_ts 기록·영속화
  → 수동 등록·동기화 직후에도 진입 봉 휩쏘 방지가 적용되도록 보장
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from src.core.trader import Trader
from src.exchange.models import Balance


def _trader() -> Trader:
    t = Trader(MagicMock())
    t._pyramid_repo = AsyncMock()
    return t


MARKET = "KRW-AERGO"


# ── _stamp_entry_ts ─────────────────────────────────────────────────────────

class TestStampEntryTs:
    def test_records_naive_utc_and_returns_matching_iso(self):
        t = _trader()
        iso = t._stamp_entry_ts(MARKET)
        assert MARKET in t._position_entry_ts
        stored = t._position_entry_ts[MARKET]
        assert stored.tzinfo is None  # naive
        assert datetime.fromisoformat(iso) == stored


# ── _forget_position ────────────────────────────────────────────────────────

class TestForgetPosition:
    async def test_clears_all_state_and_deletes_db(self):
        t = _trader()
        t._pyramid_state[MARKET] = {"entry_price": 87.0, "add_count": 0}
        t._position_highest[MARKET] = 90.0
        t._position_entry_ts[MARKET] = datetime(2026, 6, 1, 10, 14)

        await t._forget_position(MARKET)

        assert MARKET not in t._pyramid_state
        assert MARKET not in t._position_highest
        assert MARKET not in t._position_entry_ts
        t._pyramid_repo.delete.assert_awaited_once_with(MARKET)

    async def test_idempotent_when_absent(self):
        t = _trader()
        # 상태가 없어도 예외 없이 동작해야 한다
        await t._forget_position("KRW-UNKNOWN")
        t._pyramid_repo.delete.assert_awaited_once_with("KRW-UNKNOWN")


# ── set_pyramid_state (/pyramid_set) ────────────────────────────────────────

class TestSetPyramidStateStampsEntryTs:
    async def test_stamps_entry_ts_in_memory_and_persists(self):
        t = _trader()
        await t.set_pyramid_state(MARKET, entry_price=87.0, add_count=0)

        assert MARKET in t._position_entry_ts
        # save가 entry_ts(ISO 문자열, None 아님)와 함께 호출됐는지 확인
        _, kwargs = t._pyramid_repo.save.call_args
        assert kwargs["entry_ts"] is not None
        assert datetime.fromisoformat(kwargs["entry_ts"]) == t._position_entry_ts[MARKET]


# ── sync_pyramid_state (/sync) ──────────────────────────────────────────────

class TestSyncStampsEntryTs:
    async def test_synced_position_gets_entry_ts(self):
        t = _trader()
        t._upbit_ctx = AsyncMock()
        t._upbit_ctx.get_balances.return_value = [
            Balance(currency="KRW", balance=1_000_000, locked=0, avg_buy_price=1),
            Balance(currency="AERGO", balance=100.0, locked=0, avg_buy_price=87.0),
        ]

        result = await t.sync_pyramid_state()

        assert MARKET in t._position_entry_ts
        assert t._position_entry_ts[MARKET].tzinfo is None
        assert any(MARKET in a for a in result["added"])
        # save가 entry_ts와 함께 호출됐는지 확인
        _, kwargs = t._pyramid_repo.save.call_args
        assert kwargs["entry_ts"] is not None

    async def test_removed_position_clears_entry_ts(self):
        """잔고 없는 종목 제거 시 entry_ts도 정리된다(_forget_position 경유)."""
        t = _trader()
        t._pyramid_state[MARKET] = {"entry_price": 87.0, "add_count": 0}
        t._position_highest[MARKET] = 87.0
        t._position_entry_ts[MARKET] = datetime(2026, 6, 1, 10, 14)
        t._upbit_ctx = AsyncMock()
        # 잔고에 해당 코인 없음 → 제거 대상
        t._upbit_ctx.get_balances.return_value = [
            Balance(currency="KRW", balance=1_000_000, locked=0, avg_buy_price=1),
        ]

        result = await t.sync_pyramid_state()

        assert MARKET in result["removed"]
        assert MARKET not in t._position_entry_ts
        assert MARKET not in t._position_highest
        assert MARKET not in t._pyramid_state
