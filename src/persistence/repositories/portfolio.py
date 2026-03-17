"""포트폴리오 이력 저장소"""
from datetime import datetime, timezone

from src.persistence.database import Database


class PortfolioRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def snapshot(
        self,
        total_krw: float,
        coin_value: float,
        unrealized_pnl: float = 0.0,
        drawdown_pct: float = 0.0,
    ) -> None:
        """현재 포트폴리오 상태 스냅샷 저장"""
        async with self._db.transaction():
            await self._db.execute(
                """
                INSERT INTO portfolio_history (recorded_at, total_krw, coin_value, unrealized_pnl, drawdown_pct)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    total_krw,
                    coin_value,
                    unrealized_pnl,
                    drawdown_pct,
                ),
            )

    async def get_equity_curve(self, hours: int = 24) -> list[dict]:
        """최근 N시간 자산 곡선"""
        return await self._db.fetchall(
            """
            SELECT recorded_at, total_krw + coin_value AS equity
            FROM portfolio_history
            WHERE recorded_at >= datetime('now', ?)
            ORDER BY recorded_at ASC
            """,
            (f"-{hours} hours",),
        )

    async def get_peak_equity(self) -> float:
        """역대 최고 자산액 (킬스위치 낙폭 계산용)"""
        row = await self._db.fetchone(
            "SELECT MAX(total_krw + coin_value) AS peak FROM portfolio_history"
        )
        return float(row["peak"]) if row and row["peak"] else 0.0
