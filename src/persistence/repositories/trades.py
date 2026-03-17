"""매매 내역 저장소"""
from datetime import datetime, timezone

from src.persistence.database import Database


class TradeRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def record_open(
        self,
        market: str,
        side: str,
        price: float,
        volume: float,
        fee: float,
        strategy: str | None = None,
        agent_thread_id: str | None = None,
    ) -> int:
        """매수/매도 포지션 오픈 기록"""
        async with self._db.transaction():
            cursor = await self._db.execute(
                """
                INSERT INTO trades (market, side, price, volume, fee, strategy, agent_thread_id, opened_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    market, side, price, volume, fee,
                    strategy, agent_thread_id,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return cursor.lastrowid

    async def record_close(
        self,
        trade_id: int,
        close_price: float,
        pnl: float,
    ) -> None:
        """포지션 청산 기록"""
        async with self._db.transaction():
            await self._db.execute(
                """
                UPDATE trades
                SET pnl = ?, closed_at = ?, status = 'closed'
                WHERE id = ?
                """,
                (pnl, datetime.now(timezone.utc).isoformat(), trade_id),
            )

    async def get_open_trades(self, market: str | None = None) -> list[dict]:
        """미결 포지션 조회"""
        if market:
            return await self._db.fetchall(
                "SELECT * FROM trades WHERE status = 'open' AND market = ? ORDER BY opened_at DESC",
                (market,),
            )
        return await self._db.fetchall(
            "SELECT * FROM trades WHERE status = 'open' ORDER BY opened_at DESC"
        )

    async def get_recent(self, limit: int = 20) -> list[dict]:
        """최근 매매 내역"""
        return await self._db.fetchall(
            "SELECT * FROM trades ORDER BY opened_at DESC LIMIT ?",
            (limit,),
        )

    async def get_performance_summary(self, days: int = 30) -> dict:
        """최근 N일 성과 요약"""
        row = await self._db.fetchone(
            """
            SELECT
                COUNT(*) AS total_trades,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
                SUM(pnl) AS total_pnl,
                AVG(pnl) AS avg_pnl
            FROM trades
            WHERE status = 'closed'
            AND opened_at >= datetime('now', ?)
            """,
            (f"-{days} days",),
        )
        return row or {}
