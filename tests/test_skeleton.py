import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from config import PROJECT_ROOT, Settings
from db.session import create_engine, create_tables
from main import main
from middlewares.database import DatabaseMiddleware

TOKEN = "123456:local-test-token"


class SettingsTests(unittest.TestCase):
    def test_missing_and_invalid_settings_do_not_expose_token(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValidationError):
                Settings(_env_file=None)
            for token in ("", "private-invalid-token"):
                with self.assertRaises(ValidationError) as caught:
                    Settings(
                        _env_file=None, bot_token=token, owner_id=1, group_id=-1
                    )
                self.assertNotIn("private-invalid-token", str(caught.exception))

    def test_relative_database_path_and_secret_representation(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(
                _env_file=None, bot_token=TOKEN, owner_id=1, group_id=-1
            )
        self.assertEqual(settings.database_path, PROJECT_ROOT / "data/bot.sqlite3")
        self.assertNotIn(TOKEN, repr(settings))


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_persistence_foreign_keys_and_rollback_on_handler_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "test.sqlite3"
            engine = create_engine(path)
            try:
                await create_tables(engine)
                factory = async_sessionmaker(engine, expire_on_commit=False)
                async with engine.begin() as connection:
                    result = await connection.execute(text("PRAGMA foreign_keys"))
                    self.assertEqual(result.scalar_one(), 1)
                    await connection.execute(text("CREATE TABLE sample (id INTEGER)"))

                async def save(_event, data):
                    session = data["session"]
                    await session.execute(text("INSERT INTO sample VALUES (1)"))
                    await session.commit()

                async def fail(_event, data):
                    await data["session"].execute(
                        text("INSERT INTO sample VALUES (2)")
                    )
                    raise RuntimeError("handler failed")

                middleware = DatabaseMiddleware(factory)
                await middleware(save, Update(update_id=1), {})
                with self.assertRaisesRegex(RuntimeError, "handler failed"):
                    await middleware(fail, Update(update_id=2), {})
            finally:
                await engine.dispose()

            engine = create_engine(path)
            try:
                async with engine.connect() as connection:
                    result = await connection.execute(text("SELECT id FROM sample"))
                    self.assertEqual(result.scalars().all(), [1])
            finally:
                await engine.dispose()


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_polling_failure_closes_database_and_bot_session(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bot.sqlite3"
            settings = Settings(
                _env_file=None,
                bot_token=TOKEN,
                owner_id=1,
                group_id=-1,
                database_path=path,
            )
            engine = create_engine(path)
            bot = Bot(token=TOKEN)
            with (
                patch("main.Settings", return_value=settings),
                patch("main.create_engine", return_value=engine),
                patch("main.Bot", return_value=bot),
                patch.object(
                    Dispatcher, "start_polling", side_effect=RuntimeError("polling failed")
                ),
                patch.object(type(engine), "dispose", wraps=engine.dispose) as dispose,
                patch.object(bot.session, "close", wraps=bot.session.close) as close,
            ):
                with self.assertRaisesRegex(RuntimeError, "polling failed"):
                    await main()
                dispose.assert_awaited_once()
                close.assert_awaited_once()
                self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
