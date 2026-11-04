import tempfile
import unittest
from pathlib import Path

from aiogram.types import Update
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from db.session import create_engine, create_tables
from middlewares.database import DatabaseMiddleware


class DatabaseSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "nested" / "тест #1.sqlite3"
        self.engine = create_engine(self.path)
        self.addAsyncCleanup(self.engine.dispose)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.middleware = DatabaseMiddleware(self.factory)
        await create_tables(self.engine)
        async with self.engine.begin() as connection:
            await connection.execute(text("CREATE TABLE sample (id INTEGER PRIMARY KEY)"))

    async def test_success_without_commit_is_rolled_back_and_result_is_returned(self):
        marker = object()
        event = Update(update_id=1)
        data = {"settings": marker}

        async def handler(received_event, received_data):
            self.assertIs(received_event, event)
            self.assertIs(received_data, data)
            self.assertIs(received_data["settings"], marker)
            await received_data["session"].execute(text("INSERT INTO sample VALUES (1)"))
            return marker

        self.assertIs(await self.middleware(handler, event, data), marker)
        async with self.engine.connect() as connection:
            result = await connection.execute(text("SELECT id FROM sample"))
            self.assertEqual(result.scalars().all(), [])

    async def test_each_event_gets_its_own_session_and_committed_rows_persist(self):
        sessions = []

        async def handler(event, data):
            sessions.append(data["session"])
            await data["session"].execute(
                text("INSERT INTO sample VALUES (:id)"), {"id": event.update_id}
            )
            await data["session"].commit()

        for update_id in (1, 2):
            await self.middleware(handler, Update(update_id=update_id), {})
        self.assertIsNot(sessions[0], sessions[1])
        async with self.engine.connect() as connection:
            result = await connection.execute(text("SELECT id FROM sample ORDER BY id"))
            self.assertEqual(result.scalars().all(), [1, 2])

    async def test_foreign_keys_are_enforced_on_multiple_connections(self):
        async with self.engine.begin() as connection:
            await connection.execute(text(
                "CREATE TABLE child (parent_id INTEGER REFERENCES sample(id))"
            ))
        async with self.engine.connect() as first, self.engine.connect() as second:
            for connection in (first, second):
                with self.subTest(connection=connection):
                    with self.assertRaises(IntegrityError):
                        await connection.execute(text("INSERT INTO child VALUES (999)"))
                    await connection.rollback()

    async def test_recreating_tables_keeps_existing_data(self):
        async with self.engine.begin() as connection:
            await connection.execute(text("INSERT INTO sample VALUES (1)"))
        await create_tables(self.engine)
        async with self.engine.connect() as connection:
            result = await connection.execute(text("SELECT id FROM sample"))
            self.assertEqual(result.scalars().all(), [1])
        self.assertTrue(self.path.is_file())
