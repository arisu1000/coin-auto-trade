"""매수 제외 마켓 영속화 저장소"""
from src.persistence.database import Database


class ExcludedMarketsRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(self, market: str, reason: str = "") -> None:
        await self._db.execute(
            """
            INSERT INTO excluded_markets(market, reason)
            VALUES(?, ?)
            ON CONFLICT(market) DO UPDATE SET reason = excluded.reason
            """,
            (market, reason),
        )
        await self._db._conn.commit()

    async def remove(self, market: str) -> None:
        await self._db.execute("DELETE FROM excluded_markets WHERE market = ?", (market,))
        await self._db._conn.commit()

    async def load_all(self) -> dict[str, str]:
        """저장된 제외 마켓 {market: reason} 반환"""
        rows = await self._db.fetchall("SELECT market, reason FROM excluded_markets")
        return {row["market"]: row["reason"] for row in rows}
