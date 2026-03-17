"""
SQLite 스키마 마이그레이션 관리

버전 순서대로 실행하며 PRAGMA user_version으로 진행 상황 추적.
"""
import structlog

from src.persistence.database import Database

logger = structlog.get_logger(__name__)

# (version, description, sql) 순서로 정의
MIGRATIONS: list[tuple[int, str, str]] = [
    (
        1,
        "baseline_schema",
        """
        CREATE TABLE IF NOT EXISTS trades (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            market          TEXT NOT NULL,
            side            TEXT NOT NULL CHECK(side IN ('bid','ask')),
            price           REAL NOT NULL,
            volume          REAL NOT NULL,
            fee             REAL NOT NULL DEFAULT 0,
            strategy        TEXT,
            agent_thread_id TEXT,
            pnl             REAL,
            opened_at       TEXT NOT NULL,
            closed_at       TEXT,
            status          TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed','cancelled'))
        );

        CREATE INDEX IF NOT EXISTS idx_trades_market ON trades(market);
        CREATE INDEX IF NOT EXISTS idx_trades_opened_at ON trades(opened_at);

        CREATE TABLE IF NOT EXISTS portfolio_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at     TEXT NOT NULL,
            total_krw       REAL NOT NULL,
            coin_value      REAL NOT NULL DEFAULT 0,
            unrealized_pnl  REAL DEFAULT 0,
            drawdown_pct    REAL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_portfolio_recorded_at ON portfolio_history(recorded_at);

        CREATE TABLE IF NOT EXISTS bot_logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            level       TEXT NOT NULL CHECK(level IN ('DEBUG','INFO','WARNING','ERROR','CRITICAL')),
            module      TEXT NOT NULL,
            message     TEXT NOT NULL,
            context     TEXT,
            created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );

        CREATE INDEX IF NOT EXISTS idx_bot_logs_created_at ON bot_logs(created_at);
        CREATE INDEX IF NOT EXISTS idx_bot_logs_level ON bot_logs(level);

        CREATE TABLE IF NOT EXISTS agent_checkpoints (
            thread_id   TEXT NOT NULL PRIMARY KEY,
            checkpoint  TEXT NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );
        """,
    ),
    (
        2,
        "kill_switch_state",
        """
        CREATE TABLE IF NOT EXISTS kill_switch_state (
            key        TEXT NOT NULL PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        )
        """,
    ),
]


async def run_migrations(db: Database) -> None:
    """미적용 마이그레이션을 순서대로 실행"""
    # 현재 스키마 버전 확인
    cursor = await db.execute("PRAGMA user_version")
    row = await cursor.fetchone()
    current_version = row[0] if row else 0

    pending = [(v, desc, sql) for v, desc, sql in MIGRATIONS if v > current_version]
    if not pending:
        logger.info("migrations_up_to_date", version=current_version)
        return

    for version, description, sql in pending:
        logger.info("applying_migration", version=version, description=description)
        async with db.transaction():
            # SQL 블록 실행 (여러 문장 포함 가능)
            for statement in sql.strip().split(";"):
                statement = statement.strip()
                if statement:
                    await db.execute(statement)
            await db.execute(f"PRAGMA user_version = {version}")

    logger.info("migrations_completed", final_version=version)
