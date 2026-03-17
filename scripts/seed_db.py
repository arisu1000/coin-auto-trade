"""
데이터베이스 초기화 스크립트

컨테이너 시작 시 entrypoint.sh에서 호출됨.
마이그레이션 적용 + 초기 데이터 삽입.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.settings import get_settings
from src.config.logging_config import configure_logging
from src.persistence.database import Database
from src.persistence.migrations import run_migrations


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    print(f"[seed_db] DB 경로: {settings.db_path}")
    db = Database(settings.db_path)
    await db.connect()
    await run_migrations(db)
    await db.close()
    print("[seed_db] 완료")


if __name__ == "__main__":
    asyncio.run(main())
