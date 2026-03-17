"""봇 로그 저장소"""
import json
from datetime import timezone, datetime

from src.persistence.database import Database


class LogRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def write(
        self,
        level: str,
        module: str,
        message: str,
        context: dict | None = None,
    ) -> None:
        async with self._db.transaction():
            await self._db.execute(
                """
                INSERT INTO bot_logs (level, module, message, context, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    level.upper(),
                    module,
                    message,
                    json.dumps(context, ensure_ascii=False) if context else None,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    async def get_recent(self, limit: int = 50, level: str | None = None) -> list[dict]:
        if level:
            return await self._db.fetchall(
                "SELECT * FROM bot_logs WHERE level = ? ORDER BY created_at DESC LIMIT ?",
                (level.upper(), limit),
            )
        return await self._db.fetchall(
            "SELECT * FROM bot_logs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    async def get_errors(self, hours: int = 1) -> list[dict]:
        """최근 N시간 에러 로그"""
        return await self._db.fetchall(
            """
            SELECT * FROM bot_logs
            WHERE level IN ('ERROR','CRITICAL')
            AND created_at >= datetime('now', ?)
            ORDER BY created_at DESC
            """,
            (f"-{hours} hours",),
        )
