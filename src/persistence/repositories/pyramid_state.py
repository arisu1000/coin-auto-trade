"""
피라미딩 전략 상태 영속화 저장소

entry_price, add_count를 SQLite에 저장하여
앱 재시작 후에도 중복 매수 없이 이어서 운영한다.
"""
import json

from src.persistence.database import Database


class PyramidStateRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def save(self, market: str, entry_price: float, add_count: int) -> None:
        await self._db.execute(
            """
            INSERT INTO pyramid_state(market, entry_price, add_count, updated_at)
            VALUES(?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            ON CONFLICT(market) DO UPDATE SET
                entry_price = excluded.entry_price,
                add_count   = excluded.add_count,
                updated_at  = excluded.updated_at
            """,
            (market, entry_price, add_count),
        )
        await self._db._conn.commit()

    async def delete(self, market: str) -> None:
        await self._db.execute("DELETE FROM pyramid_state WHERE market = ?", (market,))
        await self._db._conn.commit()

    async def load_all(self) -> dict[str, dict]:
        """저장된 전체 상태를 {market: {entry_price, add_count}} 형태로 반환"""
        rows = await self._db.fetchall(
            "SELECT market, entry_price, add_count FROM pyramid_state"
        )
        return {
            row["market"]: {"entry_price": row["entry_price"], "add_count": row["add_count"]}
            for row in rows
        }
