"""
영속성 통합 테스트

인메모리 SQLite를 사용하여 실제 DB 레이어 동작 검증
"""
import pytest

from src.persistence.repositories.checkpoints import CheckpointRepository
from src.persistence.repositories.logs import LogRepository
from src.persistence.repositories.portfolio import PortfolioRepository
from src.persistence.repositories.trades import TradeRepository


class TestTradeRepository:
    async def test_record_and_retrieve_open_trade(self, db):
        repo = TradeRepository(db)
        trade_id = await repo.record_open(
            market="KRW-BTC",
            side="bid",
            price=50_000_000,
            volume=0.001,
            fee=25.0,
            strategy="momentum",
        )
        assert trade_id > 0

        trades = await repo.get_open_trades("KRW-BTC")
        assert len(trades) == 1
        assert trades[0]["market"] == "KRW-BTC"
        assert trades[0]["status"] == "open"

    async def test_close_trade(self, db):
        repo = TradeRepository(db)
        trade_id = await repo.record_open(
            market="KRW-BTC", side="bid",
            price=50_000_000, volume=0.001, fee=25.0,
        )
        await repo.record_close(trade_id, close_price=51_000_000, pnl=975.0)

        open_trades = await repo.get_open_trades("KRW-BTC")
        assert len(open_trades) == 0

        all_recent = await repo.get_recent(limit=10)
        closed = [t for t in all_recent if t["status"] == "closed"]
        assert len(closed) == 1
        assert closed[0]["pnl"] == pytest.approx(975.0)

    async def test_performance_summary(self, db):
        repo = TradeRepository(db)
        # 여러 거래 추가
        for i in range(3):
            tid = await repo.record_open(
                market="KRW-BTC", side="bid",
                price=50_000_000, volume=0.001, fee=25.0,
            )
            await repo.record_close(tid, close_price=51_000_000, pnl=float(1000 * (i + 1)))

        summary = await repo.get_performance_summary(days=30)
        assert int(summary["total_trades"]) == 3


class TestPortfolioRepository:
    async def test_snapshot_and_retrieve(self, db):
        repo = PortfolioRepository(db)
        await repo.snapshot(total_krw=1_000_000, coin_value=500_000)
        await repo.snapshot(total_krw=900_000, coin_value=600_000)

        curve = await repo.get_equity_curve(hours=1)
        assert len(curve) >= 2

    async def test_peak_equity(self, db):
        repo = PortfolioRepository(db)
        await repo.snapshot(total_krw=500_000, coin_value=500_000)  # equity=1M
        await repo.snapshot(total_krw=600_000, coin_value=800_000)  # equity=1.4M
        await repo.snapshot(total_krw=400_000, coin_value=400_000)  # equity=0.8M

        peak = await repo.get_peak_equity()
        assert peak == pytest.approx(1_400_000)


class TestLogRepository:
    async def test_write_and_read(self, db):
        repo = LogRepository(db)
        await repo.write("INFO", "test_module", "테스트 메시지", {"key": "value"})

        logs = await repo.get_recent(limit=5)
        assert len(logs) == 1
        assert logs[0]["level"] == "INFO"
        assert logs[0]["message"] == "테스트 메시지"

    async def test_filter_by_level(self, db):
        repo = LogRepository(db)
        await repo.write("INFO", "mod", "info 메시지")
        await repo.write("ERROR", "mod", "error 메시지")

        errors = await repo.get_errors(hours=1)
        assert len(errors) == 1
        assert errors[0]["level"] == "ERROR"


class TestCheckpointRepository:
    async def test_save_and_load(self, db):
        repo = CheckpointRepository(db)
        state = {"judge_decision": "BUY", "bull_signal": 0.8}
        await repo.save("thread_001", state)

        loaded = await repo.load("thread_001")
        assert loaded is not None
        assert loaded["judge_decision"] == "BUY"
        assert loaded["bull_signal"] == pytest.approx(0.8)

    async def test_upsert_updates_existing(self, db):
        repo = CheckpointRepository(db)
        await repo.save("thread_001", {"decision": "HOLD"})
        await repo.save("thread_001", {"decision": "BUY"})  # 같은 thread_id

        loaded = await repo.load("thread_001")
        assert loaded["decision"] == "BUY"

    async def test_load_nonexistent_returns_none(self, db):
        repo = CheckpointRepository(db)
        result = await repo.load("nonexistent_thread")
        assert result is None

    async def test_delete(self, db):
        repo = CheckpointRepository(db)
        await repo.save("thread_001", {"data": "test"})
        await repo.delete("thread_001")
        assert await repo.load("thread_001") is None
