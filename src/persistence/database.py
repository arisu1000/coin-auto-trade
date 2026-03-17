"""
aiosqlite 기반 비동기 데이터베이스 연결 관리

WAL 모드: 트레이딩 루프와 텔레그램 봇이 동시에 읽기/쓰기 가능
PRAGMA synchronous=NORMAL: 성능과 내구성의 균형점
"""
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import aiosqlite
import structlog

logger = structlog.get_logger(__name__)


class Database:
    def __init__(self, db_path: str) -> None:
        self._path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """데이터베이스 연결 및 최적화 설정"""
        path = Path(self._path)
        if self._path != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)

        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row

        # 성능 최적화 설정
        await self._conn.execute("PRAGMA journal_mode=WAL")       # 동시 읽기 허용
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute("PRAGMA synchronous=NORMAL")     # 내구성 균형
        await self._conn.execute("PRAGMA cache_size=-64000")      # 64MB 캐시
        await self._conn.execute("PRAGMA temp_store=MEMORY")
        await self._conn.commit()

        logger.info("database_connected", path=self._path)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None
            logger.info("database_closed")

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[aiosqlite.Connection, None]:
        """ACID 트랜잭션 컨텍스트 매니저"""
        if self._conn is None:
            raise RuntimeError("데이터베이스에 연결되지 않았습니다")
        try:
            yield self._conn
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise

    async def execute(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        if self._conn is None:
            raise RuntimeError("데이터베이스에 연결되지 않았습니다")
        return await self._conn.execute(sql, params)

    async def executemany(self, sql: str, params_list: list[tuple]) -> None:
        if self._conn is None:
            raise RuntimeError("데이터베이스에 연결되지 않았습니다")
        await self._conn.executemany(sql, params_list)
        await self._conn.commit()

    async def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        cursor = await self.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def fetchone(self, sql: str, params: tuple = ()) -> dict | None:
        cursor = await self.execute(sql, params)
        row = await cursor.fetchone()
        return dict(row) if row else None
