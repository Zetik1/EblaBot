import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiogram import Bot, Dispatcher
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, Update, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from config import Settings
from db.models import Person
from db.session import create_engine, create_tables
from handlers import create_router
from middlewares.database import DatabaseMiddleware
from services.roles import Role, role_for_registration

BOT_TOKEN = "123456:local-test-token"
OWNER_ID = 42


class RoleForRegistrationTests(unittest.TestCase):
    def test_owner_gets_admin_and_everyone_else_gets_no_role(self):
        self.assertEqual(role_for_registration(42, 42), Role.ADMIN)
        self.assertIsNone(role_for_registration(7, 42))


class StartRoleTests(unittest.IsolatedAsyncioTestCase):
    """/start в личке -> Dispatcher -> register_person -> временная SQLite -> чтение новой сессией."""

    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.engine = create_engine(Path(directory.name) / "start-roles.sqlite3")
        self.addAsyncCleanup(self.engine.dispose)
        await create_tables(self.engine)

        self.bot = Bot(token=BOT_TOKEN)
        self.addAsyncCleanup(self.bot.session.close)
        self.request = AsyncMock()
        request_patch = patch.object(self.bot.session, "make_request", self.request)
        request_patch.start()
        self.addCleanup(request_patch.stop)
        self.update_id = 0
        self.use_owner(OWNER_ID)

    def use_owner(self, owner_id):
        """Пересобирает Dispatcher: OWNER_ID приходит в обработчик через settings."""
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(
                _env_file=None,
                bot_token=BOT_TOKEN,
                owner_id=owner_id,
                group_id=-100123,
            )
        self.dispatcher = Dispatcher(settings=settings)
        self.dispatcher.update.outer_middleware(
            DatabaseMiddleware(async_sessionmaker(self.engine, expire_on_commit=False))
        )
        self.dispatcher.include_router(create_router())

    async def send_start(self, user_id, first_name="Влад", last_name=None, username=None):
        self.request.reset_mock()
        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=Chat(id=user_id, type="private"),
            from_user=User(
                id=user_id,
                is_bot=False,
                first_name=first_name,
                last_name=last_name,
                username=username,
            ),
            text="/start",
        )
        self.update_id += 1
        await self.dispatcher.feed_update(
            self.bot, Update(update_id=self.update_id, message=message)
        )

    def assert_greeted(self, user_id, first_name):
        self.request.assert_awaited_once()
        reply = self.request.call_args.args[1]
        self.assertIsInstance(reply, SendMessage)
        self.assertEqual(reply.chat_id, user_id)
        self.assertIn(f"Привет, {first_name}!", reply.text)

    async def seed_person(self, telegram_id, role, first_name="Старое", username="old"):
        # Команд назначения ролей ещё нет, поэтому готовый профиль кладём в БД напрямую.
        async with AsyncSession(self.engine) as session:
            session.add(
                Person(
                    telegram_id=telegram_id,
                    first_name=first_name,
                    last_name=None,
                    username=username,
                    role=role,
                )
            )
            await session.commit()

    async def read_people(self):
        # Новая сессия: видно только то, что реально записано и закоммичено в файл.
        profiles_by_id = select(
            Person.telegram_id,
            Person.first_name,
            Person.last_name,
            Person.username,
            Person.role,  # сохранённая роль после /start читается здесь
        ).order_by(Person.telegram_id)
        async with AsyncSession(self.engine) as session:
            result = await session.execute(profiles_by_id)
            return result.all()

    async def test_new_owner_start_creates_one_admin_profile(self):
        await self.send_start(OWNER_ID, "Влад", "Иванов", "vlad")

        self.assertEqual(
            await self.read_people(), [(OWNER_ID, "Влад", "Иванов", "vlad", Role.ADMIN)]
        )
        self.assert_greeted(OWNER_ID, "Влад")

    async def test_owner_with_old_user_profile_becomes_admin_after_two_starts(self):
        await self.seed_person(OWNER_ID, Role.USER)
        self.assertEqual(
            await self.read_people(), [(OWNER_ID, "Старое", None, "old", Role.USER)]
        )

        await self.send_start(OWNER_ID, "Новое", None, "new")
        self.assertEqual(
            await self.read_people(), [(OWNER_ID, "Новое", None, "new", Role.ADMIN)]
        )

        await self.send_start(OWNER_ID, "Новое", None, "new")
        self.assertEqual(
            await self.read_people(), [(OWNER_ID, "Новое", None, "new", Role.ADMIN)]
        )
        self.assert_greeted(OWNER_ID, "Новое")

    async def test_owner_gets_admin_whatever_role_the_old_profile_had(self):
        for owner_id, old_role in ((101, Role.USER), (102, Role.MODERATOR), (103, Role.ADMIN)):
            with self.subTest(old_role=old_role):
                self.use_owner(owner_id)
                await self.seed_person(owner_id, old_role)

                await self.send_start(owner_id, "Новое")

                profile = [row for row in await self.read_people() if row[0] == owner_id]
                self.assertEqual(profile, [(owner_id, "Новое", None, None, Role.ADMIN)])

    async def test_other_person_first_start_gets_user_role(self):
        await self.send_start(7, "Гриша", "Петров", "grisha")

        self.assertEqual(
            await self.read_people(), [(7, "Гриша", "Петров", "grisha", Role.USER)]
        )
        self.assert_greeted(7, "Гриша")

    async def test_other_person_keeps_existing_role_when_name_changes(self):
        for user_id, role in ((7, Role.MODERATOR), (8, Role.ADMIN), (9, Role.USER)):
            with self.subTest(role=role):
                await self.seed_person(user_id, role)

                await self.send_start(user_id, "Новое", "Фамилия", "new")

                profile = [row for row in await self.read_people() if row[0] == user_id]
                self.assertEqual(profile, [(user_id, "Новое", "Фамилия", "new", role)])

    async def test_owner_and_other_person_get_different_roles(self):
        await self.send_start(7, "Гриша")
        await self.send_start(OWNER_ID, "Влад")

        self.assertEqual(
            await self.read_people(),
            [(7, "Гриша", None, None, Role.USER), (OWNER_ID, "Влад", None, None, Role.ADMIN)],
        )