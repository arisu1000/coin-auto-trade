"""
피라미딩 전략 상태 영속화 저장소

entry_price, add_count를 SQLite에 저장하여
앱 재시작 후에도 중복 매수 없이 이어서 운영한다.
매도 후 재진입 쿨다운(pyramid_cooldown)도 함께 관리한다.
"""
from src.persistence.database import Database


class PyramidStateRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ── pyramid_state ────────────────────────────────────────────────

    async def save(
        self,
        market: str,
        entry_price: float,
        add_count: int,
        partial_taken: bool = False,
    ) -> None:
        await self._db.execute(
            """
            INSERT INTO pyramid_state(market, entry_price, add_count, partial_taken, updated_at)
            VALUES(?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            ON CONFLICT(market) DO UPDATE SET
                entry_price   = excluded.entry_price,
                add_count     = excluded.add_count,
                partial_taken = excluded.partial_taken,
                updated_at    = excluded.updated_at
            """,
            (market, entry_price, add_count, int(partial_taken)),
        )
        await self._db._conn.commit()

    async def delete(self, market: str) -> None:
        await self._db.execute("DELETE FROM pyramid_state WHERE market = ?", (market,))
        await self._db._conn.commit()

    async def load_all(self) -> dict[str, dict]:
        """저장된 전체 상태를 {market: {entry_price, add_count, partial_taken}} 형태로 반환"""
        rows = await self._db.fetchall(
            "SELECT market, entry_price, add_count, partial_taken FROM pyramid_state"
        )
        return {
            row["market"]: {
                "entry_price": row["entry_price"],
                "add_count": row["add_count"],
                "partial_taken": bool(row["partial_taken"]),
            }
            for row in rows
        }

    # ── pyramid_cooldown ─────────────────────────────────────────────

    async def save_cooldown(self, market: str, sell_at: str) -> None:
        """매도 시각을 기록한다."""
        await self._db.execute(
            """
            INSERT INTO pyramid_cooldown(market, sell_at)
            VALUES(?, ?)
            ON CONFLICT(market) DO UPDATE SET sell_at = excluded.sell_at
            """,
            (market, sell_at),
        )
        await self._db._conn.commit()

    async def delete_cooldown(self, market: str) -> None:
        await self._db.execute("DELETE FROM pyramid_cooldown WHERE market = ?", (market,))
        await self._db._conn.commit()

    async def load_cooldowns(self) -> dict[str, str]:
        """저장된 쿨다운을 {market: sell_at(ISO문자열)} 형태로 반환"""
        rows = await self._db.fetchall("SELECT market, sell_at FROM pyramid_cooldown")
        return {row["market"]: row["sell_at"] for row in rows}
