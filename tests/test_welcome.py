import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage
from aiogram.types import (
    Chat,
    ChatMemberAdministrator,
    ChatMemberBanned,
    ChatMemberLeft,
    ChatMemberMember,
    ChatMemberRestricted,
    ChatMemberUpdated,
    Update,
    User,
)
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from config import Settings
from db.models import Person
from db.session import create_engine, create_tables
from handlers import create_router
from middlewares.database import DatabaseMiddleware

BOT_TOKEN = "123456:local-test-token"
GROUP_ID = -100123
OTHER_GROUP_ID = -100999
TOPIC_ID = 456


def administrator(user):
    # Обязательные поля администратора зависят от версии aiogram, поэтому заполняем их сами.
    flags = {
        name: True
        for name, field in ChatMemberAdministrator.model_fields.items()
        if field.is_required() and name != "user"
    }
    return ChatMemberAdministrator(user=user, **flags)


def restricted(user, *, is_member):
    flags = {
        name: False
        for name, field in ChatMemberRestricted.model_fields.items()
        if field.is_required() and name not in ("user", "is_member", "until_date")
    }
    return ChatMemberRestricted(user=user, is_member=is_member, until_date=0, **flags)


class WelcomeTests(unittest.IsolatedAsyncioTestCase):
    """chat_member -> Dispatcher -> приветствие в Telegram (подменено) и запись в временной SQLite."""

    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.engine = create_engine(Path(directory.name) / "welcome.sqlite3")
        self.addAsyncCleanup(self.engine.dispose)
        await create_tables(self.engine)

        self.bot = Bot(token=BOT_TOKEN)
        self.addAsyncCleanup(self.bot.session.close)
        self.request = AsyncMock()
        request_patch = patch.object(self.bot.session, "make_request", self.request)
        request_patch.start()
        self.addCleanup(request_patch.stop)
        self.update_id = 0
        self.use_settings()  # по умолчанию тема не задана

    def use_settings(self, **overrides):
        """Пересобирает Dispatcher с нужными настройками (например, welcome_theme_id)."""
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(
                _env_file=None,
                bot_token=BOT_TOKEN,
                owner_id=1,
                group_id=GROUP_ID,
                **overrides,
            )
        self.dispatcher = Dispatcher(settings=settings)
        self.dispatcher.update.outer_middleware(
            DatabaseMiddleware(async_sessionmaker(self.engine, expire_on_commit=False))
        )
        self.dispatcher.include_router(create_router())

    async def send_member_update(
        self,
        old_status,
        new_status,
        *,
        user_id=7,
        first_name="Гриша",
        is_bot=False,
        chat_id=GROUP_ID,
        inviter_name="Пригласивший",
    ):
        """Статусы: left, member, administrator, kicked, restricted_in (is_member=True), restricted_out (is_member=False)."""
        user = User(id=user_id, is_bot=is_bot, first_name=first_name)
        statuses = {
            "left": lambda: ChatMemberLeft(user=user),
            "member": lambda: ChatMemberMember(user=user),
            "administrator": lambda: administrator(user),
            "kicked": lambda: ChatMemberBanned(user=user, until_date=0),
            "restricted_in": lambda: restricted(user, is_member=True),
            "restricted_out": lambda: restricted(user, is_member=False),
        }
        self.update_id += 1
        event = ChatMemberUpdated(
            chat=Chat(id=chat_id, type="supergroup"),
            # тот, кто добавил: имя в приветствии должно быть именем вошедшего, а не его
            from_user=User(id=42, is_bot=False, first_name=inviter_name),
            date=datetime.now(timezone.utc),
            old_chat_member=statuses[old_status](),
            new_chat_member=statuses[new_status](),
        )
        await self.dispatcher.feed_update(
            self.bot, Update(update_id=self.update_id, chat_member=event)
        )

    async def join(self, **kwargs):
        await self.send_member_update("left", "member", **kwargs)

    def sent(self) -> list[SendMessage]:
        return [call.args[1] for call in self.request.await_args_list]

    async def saved_ids(self):
        # Новая сессия: видно только то, что реально записано и закоммичено в файл.
        async with AsyncSession(self.engine) as session:
            result = await session.execute(
                select(Person.telegram_id).order_by(Person.telegram_id)
            )
            return [row[0] for row in result.all()]

    async def test_join_sends_greeting_to_group_and_configured_topic(self):
        self.use_settings(welcome_theme_id=TOPIC_ID)

        await self.join(first_name="Гриша")

        (greeting,) = self.sent()
        self.assertIsInstance(greeting, SendMessage)
        self.assertEqual(greeting.chat_id, GROUP_ID)
        self.assertEqual(greeting.message_thread_id, TOPIC_ID)
        self.assertIn("Гриша", greeting.text)
        self.assertEqual(await self.saved_ids(), [7])

    async def test_join_without_topic_sends_plain_group_message(self):
        await self.join(first_name="Гриша")

        (greeting,) = self.sent()
        self.assertEqual(greeting.chat_id, GROUP_ID)
        self.assertIsNone(greeting.message_thread_id)
        self.assertNotIn("message_thread_id", greeting.model_dump(exclude_none=True))
        self.assertIn("Гриша", greeting.text)

    async def test_greeting_names_the_new_member_not_the_inviter(self):
        await self.join(first_name="Гриша", inviter_name="Пригласивший")

        (greeting,) = self.sent()
        self.assertIn("Гриша", greeting.text)
        self.assertNotIn("Пригласивший", greeting.text)

    async def test_greeting_text_has_no_stray_whitespace_or_quotes(self):
        await self.join(first_name="Гриша")

        (greeting,) = self.sent()
        self.assertEqual(greeting.text, greeting.text.strip())
        self.assertNotIn('"', greeting.text)
        self.assertNotRegex(greeting.text, r"(?m)^[ \t]+")  # отступы в начале строк

    async def test_every_real_join_gets_one_greeting(self):
        self.use_settings(welcome_theme_id=TOPIC_ID)
        joins = (
            ("left", "member"),
            ("kicked", "member"),
            ("restricted_out", "member"),
            ("restricted_out", "restricted_in"),
        )
        for index, (old_status, new_status) in enumerate(joins, start=1):
            with self.subTest(old=old_status, new=new_status):
                await self.send_member_update(
                    old_status, new_status, user_id=index, first_name=f"Имя{index}"
                )
                self.assertEqual(len(self.sent()), index)
                self.assertIn(f"Имя{index}", self.sent()[-1].text)
        self.assertEqual(await self.saved_ids(), [1, 2, 3, 4])

    async def test_rejoin_greets_again(self):
        await self.join()
        await self.send_member_update("member", "left")
        self.assertEqual(len(self.sent()), 1)  # выход приветствия не создаёт

        await self.join()
        self.assertEqual(len(self.sent()), 2)
        self.assertEqual(await self.saved_ids(), [7])  # запись одна

    async def test_no_greeting_without_a_real_join(self):
        self.use_settings(welcome_theme_id=TOPIC_ID)
        cases = (
            ("вход бота", dict(old="left", new="member", is_bot=True, user_id=555)),
            ("вход в другую группу", dict(old="left", new="member", chat_id=OTHER_GROUP_ID)),
            ("выход", dict(old="member", new="left")),
            ("исключение", dict(old="member", new="kicked")),
            ("смена роли на админа", dict(old="member", new="administrator")),
            ("снятие админа", dict(old="administrator", new="member")),
            ("ограничение участника, оставшегося в группе", dict(old="member", new="restricted_in")),
            ("снятие ограничения у участника", dict(old="restricted_in", new="member")),
            ("ограничение с выходом", dict(old="member", new="restricted_out")),
        )
        for title, case in cases:
            with self.subTest(title):
                case = dict(case)
                old, new = case.pop("old"), case.pop("new")
                await self.send_member_update(old, new, **case)
                self.request.assert_not_awaited()
        self.assertEqual(await self.saved_ids(), [])

    async def test_database_failure_sends_no_greeting(self):
        # Ломаем настоящую БД: save_person упадёт реальной ошибкой SQLAlchemy.
        async with self.engine.begin() as connection:
            await connection.execute(text(f'DROP TABLE "{Person.__tablename__}"'))

        with self.assertLogs(level="ERROR") as logs:
            await self.join()

        self.assertIn("id: 7", logs.output[0])
        self.request.assert_not_awaited()

    async def test_telegram_error_is_logged_and_profile_stays_saved(self):
        self.use_settings(welcome_theme_id=TOPIC_ID)
        self.request.side_effect = TelegramBadRequest(
            method=SendMessage(chat_id=GROUP_ID, text="x"),
            message="Bad Request: message thread not found",
        )

        with self.assertLogs(level="ERROR") as logs:
            await self.join()  # обработчик не должен падать

        self.assertIn("id: 7", logs.output[0])
        self.assertEqual(await self.saved_ids(), [7])