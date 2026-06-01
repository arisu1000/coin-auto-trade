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
        highest_price: float = 0.0,
        entry_ts: str | None = None,
    ) -> None:
        # entry_ts가 None이면 기존 값을 보존(COALESCE)한다. 최고가·부분익절 갱신 등
        # entry_ts를 모르는 호출부가 진입 시각을 지우지 않도록 하기 위함.
        await self._db.execute(
            """
            INSERT INTO pyramid_state(market, entry_price, add_count, partial_taken, highest_price, entry_ts, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            ON CONFLICT(market) DO UPDATE SET
                entry_price   = excluded.entry_price,
                add_count     = excluded.add_count,
                partial_taken = excluded.partial_taken,
                highest_price = excluded.highest_price,
                entry_ts      = COALESCE(excluded.entry_ts, pyramid_state.entry_ts),
                updated_at    = excluded.updated_at
            """,
            (market, entry_price, add_count, int(partial_taken), highest_price, entry_ts),
        )
        await self._db._conn.commit()

    async def delete(self, market: str) -> None:
        await self._db.execute("DELETE FROM pyramid_state WHERE market = ?", (market,))
        await self._db._conn.commit()

    async def load_all(self) -> dict[str, dict]:
        """저장된 전체 상태를 {market: {entry_price, add_count, partial_taken, highest_price, entry_ts}} 형태로 반환"""
        rows = await self._db.fetchall(
            "SELECT market, entry_price, add_count, partial_taken, highest_price, entry_ts FROM pyramid_state"
        )
        return {
            row["market"]: {
                "entry_price": row["entry_price"],
                "add_count": row["add_count"],
                "partial_taken": bool(row["partial_taken"]),
                "highest_price": row["highest_price"] or 0.0,
                "entry_ts": row["entry_ts"],  # ISO 문자열 또는 None
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
