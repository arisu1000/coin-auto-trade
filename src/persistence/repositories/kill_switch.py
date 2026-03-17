"""
킬 스위치 상태 영속화 저장소

macro_active, manual_halt, peak_equity, micro_blocked_markets 를
SQLite에 저장하여 앱 재시작 후에도 복원한다.
"""
import json

from src.persistence.database import Database


class KillSwitchRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def save(self, state: dict) -> None:
        """상태 딕셔너리를 key-value 형태로 upsert한다."""
        for key, value in state.items():
            await self._db.execute(
                """
                INSERT INTO kill_switch_state(key, value, updated_at)
                VALUES(?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                ON CONFLICT(key) DO UPDATE SET
                    value      = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, json.dumps(value)),
            )
        await self._db._conn.commit()

    async def load(self) -> dict:
        """저장된 모든 상태를 딕셔너리로 반환한다."""
        rows = await self._db.fetchall("SELECT key, value FROM kill_switch_state")
        return {row["key"]: json.loads(row["value"]) for row in rows}
