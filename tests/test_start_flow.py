import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiogram import Bot, Dispatcher
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, Update, User
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from config import Settings
from db.models import Person
from db.session import create_engine, create_tables
from handlers import create_router
from middlewares.database import DatabaseMiddleware


class StartFlowTests(unittest.IsolatedAsyncioTestCase):
    """Сообщение -> Dispatcher -> /start -> настоящая SQLite -> чтение новой сессией."""

    async def asyncSetUp(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(
                _env_file=None,
                bot_token="123456:local-test-token",
                owner_id=1,
                group_id=-100123,
            )
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.engine = create_engine(Path(directory.name) / "start-flow.sqlite3")
        self.addAsyncCleanup(self.engine.dispose)
        await create_tables(self.engine)

        self.dispatcher = Dispatcher(settings=settings)
        self.dispatcher.update.outer_middleware(
            DatabaseMiddleware(async_sessionmaker(self.engine, expire_on_commit=False))
        )
        self.dispatcher.include_router(create_router())
        self.bot = Bot(token=settings.bot_token.get_secret_value())
        self.bot._me = User(
            id=123456, is_bot=True, first_name="Test", username="ebla_test_bot"
        )
        self.addAsyncCleanup(self.bot.session.close)
        self.request = AsyncMock()
        request_patch = patch.object(self.bot.session, "make_request", self.request)
        request_patch.start()
        self.addCleanup(request_patch.stop)

    async def send_start(
        self,
        *,
        user_id=42,
        first_name="Влад",
        last_name=None,
        username=None,
        chat_type="private",
        chat_id=None,
    ):
        self.request.reset_mock()
        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=Chat(id=user_id if chat_id is None else chat_id, type=chat_type),
            from_user=User(
                id=user_id,
                is_bot=False,
                first_name=first_name,
                last_name=last_name,
                username=username,
            ),
            text="/start",
        )
        await self.dispatcher.feed_update(self.bot, Update(update_id=1, message=message))

    def sent_reply(self, *, chat_id):
        self.request.assert_awaited_once()
        method = self.request.call_args.args[1]
        self.assertIsInstance(method, SendMessage)
        self.assertEqual(method.chat_id, chat_id)
        return method

    async def read_people(self):
        # Новая сессия: видно только то, что реально записано и закоммичено в файл.
        profiles_by_id = select(
            Person.telegram_id,
            Person.first_name,
            Person.last_name,
            Person.username,
        ).order_by(Person.telegram_id)
        async with AsyncSession(self.engine) as session:
            result = await session.execute(profiles_by_id)
            return result.all()

    async def test_start_saves_person_and_greets_in_private_chat(self):
        await self.send_start(first_name="Влад", last_name="Иванов", username="vlad")

        self.assertEqual(await self.read_people(), [(42, "Влад", "Иванов", "vlad")])
        reply = self.sent_reply(chat_id=42)
        self.assertIn("Привет, Влад!", reply.text)
        self.assertIn("/ping", reply.text)

    async def test_start_stores_missing_optional_fields_as_null(self):
        await self.send_start(first_name="Гриша")

        self.assertEqual(await self.read_people(), [(42, "Гриша", None, None)])
        self.sent_reply(chat_id=42)

    async def test_repeated_start_updates_and_clears_fields_without_duplicates(self):
        await self.send_start(first_name="Гриша", last_name="Иванов", username="grisha")
        await self.send_start(first_name="Григорий", last_name="Петров", username="grigory")
        self.assertEqual(
            await self.read_people(), [(42, "Григорий", "Петров", "grigory")]
        )

        await self.send_start(first_name="Григорий")
        self.assertEqual(await self.read_people(), [(42, "Григорий", None, None)])

        await self.send_start(first_name="Григорий")
        self.assertEqual(await self.read_people(), [(42, "Григорий", None, None)])
        self.assertIn("Привет, Григорий!", self.sent_reply(chat_id=42).text)

    async def test_different_users_are_stored_separately(self):
        await self.send_start(user_id=1, first_name="Первый")
        await self.send_start(user_id=2**40, first_name="Второй", username="second")

        self.assertEqual(
            await self.read_people(),
            [(1, "Первый", None, None), (2**40, "Второй", None, "second")],
        )

    async def test_start_in_group_is_ignored_and_does_not_touch_database(self):
        for chat_type in ("group", "supergroup"):
            with self.subTest(chat_type=chat_type):
                await self.send_start(chat_type=chat_type, chat_id=-100123)
                self.request.assert_not_awaited()
                self.assertEqual(await self.read_people(), [])

    async def test_database_failure_tells_user_to_contact_admin(self):
        # Ломаем настоящую БД: save_person упадёт реальной ошибкой SQLAlchemy.
        async with self.engine.begin() as connection:
            await connection.execute(text(f'DROP TABLE "{Person.__tablename__}"'))

        with self.assertLogs(level="ERROR") as logs:
            await self.send_start(first_name="Влад")

        self.assertIn("Не удалось сохранить пользователя 42", logs.output[0])
        reply = self.sent_reply(chat_id=42)
        self.assertIn("администратору", reply.text)
        self.assertNotIn("Привет", reply.text)
