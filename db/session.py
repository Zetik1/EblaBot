import asyncio
from pathlib import Path
from threading import Lock

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


_migration_lock = Lock()


def create_engine(database_path: Path) -> AsyncEngine:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    url = URL.create("sqlite+aiosqlite", database=str(database_path))
    engine = create_async_engine(url)

    @event.listens_for(engine.sync_engine, "connect")
    def enable_foreign_keys(connection, _record) -> None:
        cursor = connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    return engine


def upgrade_database(database_url: URL) -> None:
    project_root = Path(__file__).resolve().parents[1]
    config = Config(str(project_root / "alembic.ini"))
    config.attributes["database_url"] = database_url
    # Alembic's environment proxy is shared by calls within the same process.
    with _migration_lock:
        command.upgrade(config, "head")


async def create_tables(engine: AsyncEngine) -> None:
    database_url = engine.url.set(drivername="sqlite")
    await asyncio.to_thread(upgrade_database, database_url)
