"""LangGraph 에이전트 체크포인트 저장소"""
import json
from datetime import datetime, timezone

from src.persistence.database import Database


class CheckpointRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def save(self, thread_id: str, state: dict) -> None:
        """에이전트 상태를 JSON으로 직렬화하여 저장 (UPSERT)"""
        now = datetime.now(timezone.utc).isoformat()
        async with self._db.transaction():
            await self._db.execute(
                """
                INSERT INTO agent_checkpoints (thread_id, checkpoint, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(thread_id) DO UPDATE SET
                    checkpoint = excluded.checkpoint,
                    updated_at = excluded.updated_at
                """,
                (thread_id, json.dumps(state, ensure_ascii=False, default=str), now, now),
            )

    async def load(self, thread_id: str) -> dict | None:
        """저장된 에이전트 상태 복원"""
        row = await self._db.fetchone(
            "SELECT checkpoint FROM agent_checkpoints WHERE thread_id = ?",
            (thread_id,),
        )
        if row:
            return json.loads(row["checkpoint"])
        return None

    async def delete(self, thread_id: str) -> None:
        async with self._db.transaction():
            await self._db.execute(
                "DELETE FROM agent_checkpoints WHERE thread_id = ?",
                (thread_id,),
            )
