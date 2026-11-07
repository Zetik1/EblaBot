from pathlib import Path

from sqlalchemy import URL, event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from db.models import Base


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


async def create_tables(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        # #13. Проверь, что create_all делает с уже существующей таблицей.
        # Как понять, что старый файл БД пора обновить? Что произойдёт при
        # следующем запуске, если нужная колонка уже есть?
