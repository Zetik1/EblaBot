import os
import runpy
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiogram import Bot, Dispatcher
from aiogram.types import Chat, Message, Update, User

from config import PROJECT_ROOT, Settings
from db.session import create_engine
from main import main


class ApplicationStartupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.settings = Settings(
            _env_file=None, bot_token="123456:local-test-token", owner_id=1,
            group_id=-100123, database_path=Path(directory.name) / "bot.sqlite3",
            telegram_proxy_url="socks5://127.0.0.1:1080",
        )

    async def test_missing_settings_stop_before_database_or_bot_creation(self):
        def read_settings():
            return Settings(_env_file=None)

        with (
            patch("main.Settings", side_effect=read_settings),
            patch("main.create_engine") as engine,
            patch("main.Bot") as bot,
        ):
            with self.assertRaisesRegex(SystemExit, r"Проверьте настройки \.env"):
                await main()
        engine.assert_not_called()
        bot.assert_not_called()

    async def test_table_creation_failure_disposes_engine_before_polling(self):
        engine = create_engine(self.settings.database_path)
        with (
            patch("main.Settings", return_value=self.settings),
            patch("main.create_engine", return_value=engine),
            patch("main.create_tables", side_effect=RuntimeError("database unavailable")),
            patch("main.Bot") as bot,
            patch.object(type(engine), "dispose", wraps=engine.dispose) as dispose,
        ):
            with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                await main()
            dispose.assert_awaited_once()
            bot.assert_not_called()

    async def test_session_creation_failure_disposes_engine(self):
        engine = create_engine(self.settings.database_path)
        with (
            patch("main.Settings", return_value=self.settings),
            patch("main.create_engine", return_value=engine),
            patch("main.AiohttpSession", side_effect=RuntimeError("invalid proxy")),
            patch.object(type(engine), "dispose", wraps=engine.dispose) as dispose,
        ):
            with self.assertRaisesRegex(RuntimeError, "invalid proxy"):
                await main()
            dispose.assert_awaited_once()

    async def test_startup_routes_group_command_and_closes_resources_on_normal_stop(self):
        engine = create_engine(self.settings.database_path)
        bot = Bot(token=self.settings.bot_token.get_secret_value())
        request = AsyncMock()

        async def polling(dispatcher, actual_bot, **kwargs):
            self.assertIs(actual_bot, bot)
            self.assertEqual(kwargs["allowed_updates"], [
                "message", "callback_query", "chat_member", "my_chat_member"
            ])
            self.assertFalse(kwargs["close_bot_session"])
            message = Message(
                message_id=1, date=datetime.now(timezone.utc),
                chat=Chat(id=self.settings.group_id, type="supergroup"),
                from_user=User(id=42, is_bot=False, first_name="Test"), text="/ping",
                message_thread_id=456, is_topic_message=True,
            )
            await dispatcher.feed_update(actual_bot, Update(update_id=1, message=message))

        with (
            patch("main.Settings", return_value=self.settings),
            patch("main.create_engine", return_value=engine),
            patch("main.AiohttpSession") as session,
            patch("main.Bot", return_value=bot),
            patch.object(Dispatcher, "start_polling", polling),
            patch.object(bot.session, "make_request", request),
            patch.object(bot.session, "close", wraps=bot.session.close) as close,
            patch.object(type(engine), "dispose", wraps=engine.dispose) as dispose,
        ):
            await main()
            session.assert_called_once_with(proxy=self.settings.telegram_proxy_url)
            request.assert_awaited_once()
            reply = request.call_args.args[1]
            self.assertEqual((reply.chat_id, reply.text, reply.message_thread_id), (
                self.settings.group_id, "pong", 456
            ))
            close.assert_awaited_once()
            dispose.assert_awaited_once()


class ConsoleEntrypointTests(unittest.TestCase):
    def test_console_start_and_keyboard_interrupt_close_bot_session(self):
        for interrupt in (False, True):
            with self.subTest(interrupt=interrupt), tempfile.TemporaryDirectory() as directory:
                with patch.dict(os.environ, {}, clear=True):
                    settings = Settings(
                        _env_file=None, bot_token="123456:local-test-token", owner_id=1,
                        group_id=-1, database_path=Path(directory) / "bot.sqlite3",
                    )
                with (
                    patch("config.Settings", return_value=settings),
                    patch.object(
                        Dispatcher, "start_polling", new_callable=AsyncMock,
                        side_effect=KeyboardInterrupt if interrupt else None,
                    ) as polling,
                    patch(
                        "aiogram.client.session.aiohttp.AiohttpSession.close",
                        new_callable=AsyncMock,
                    ) as close,
                    patch(
                        "aiogram.client.session.aiohttp.AiohttpSession.make_request",
                        side_effect=AssertionError("Unexpected network request"),
                    ),
                ):
                    runpy.run_path(str(PROJECT_ROOT / "main.py"), run_name="__main__")
                polling.assert_awaited_once()
                close.assert_awaited_once()
                self.assertTrue(settings.database_path.is_file())
