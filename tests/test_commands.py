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
from services.roles import Role

OWNER_ID = 1
ADMINCHECK_ALLOWED = "Вам доступны права администратора."
ADMINCHECK_DENIED = "Данная команда доступна только администратору."


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(
                _env_file=None,
                bot_token="123456:local-test-token",
                owner_id=OWNER_ID,
                group_id=-100123,
            )
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.engine = create_engine(Path(directory.name) / "commands.sqlite3")
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

    async def send_message(
        self,
        text,
        *,
        user_id=42,
        chat_type="private",
        chat_id=None,
        thread_id=None,
        is_topic_message=None,
        first_name="Влад",
    ):
        self.request.reset_mock()
        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=Chat(id=user_id if chat_id is None else chat_id, type=chat_type),
            from_user=User(id=user_id, is_bot=False, first_name=first_name),
            text=text,
            message_thread_id=thread_id,
            is_topic_message=is_topic_message,
        )
        await self.dispatcher.feed_update(self.bot, Update(update_id=1, message=message))

    def assert_reply(self, *, chat_id, text=None, thread_id=None):
        self.request.assert_awaited_once()
        method = self.request.call_args.args[1]
        self.assertIsInstance(method, SendMessage)
        self.assertEqual(method.chat_id, chat_id)
        self.assertEqual(method.message_thread_id, thread_id)
        if text is not None:
            self.assertEqual(method.text, text)
        return method

    async def assert_no_people_saved(self):
        async with AsyncSession(self.engine) as session:
            result = await session.execute(select(Person.telegram_id))
            self.assertEqual(result.all(), [])

    async def test_start_greets_user_and_mentions_ping_in_private_chat(self):
        for name in ("Влад", "Гриша"):
            with self.subTest(name=name):
                await self.send_message("/start", first_name=name)
                reply = self.assert_reply(chat_id=42)
                self.assertIn(f"Привет, {name}!", reply.text)
                self.assertIn("/ping", reply.text)

    async def test_admincheck_uses_the_senders_saved_role(self):
        admin_id, moderator_id, user_id = 42, 43, 44
        async with AsyncSession(self.engine) as session:
            session.add_all([
                Person(telegram_id=admin_id, first_name="Влад", role=Role.ADMIN),
                Person(telegram_id=moderator_id, first_name="Гриша", role=Role.MODERATOR),
                Person(telegram_id=user_id, first_name="Иван", role=Role.USER),
            ])
            await session.commit()

        for sender_id, expected_reply in (
            (admin_id, ADMINCHECK_ALLOWED),
            (moderator_id, ADMINCHECK_DENIED),
            (user_id, ADMINCHECK_DENIED),
            (OWNER_ID, ADMINCHECK_DENIED),
        ):
            with self.subTest(sender_id=sender_id):
                await self.send_message("/admincheck", user_id=sender_id)
                self.assert_reply(chat_id=sender_id, text=expected_reply)

    async def test_admincheck_after_start_for_owner_and_regular_user(self):
        for user_id, expected_reply in (
            (OWNER_ID, ADMINCHECK_ALLOWED),
            (42, ADMINCHECK_DENIED),
        ):
            with self.subTest(user_id=user_id):
                await self.send_message("/start", user_id=user_id)
                await self.send_message("/admincheck", user_id=user_id)
                self.assert_reply(chat_id=user_id, text=expected_reply)

    async def test_admincheck_is_ignored_in_groups_even_for_admin(self):
        await self.send_message("/start", user_id=OWNER_ID)
        for chat_type in ("group", "supergroup"):
            with self.subTest(chat_type=chat_type):
                await self.send_message(
                    "/admincheck", user_id=OWNER_ID, chat_type=chat_type, chat_id=-100123
                )
                self.request.assert_not_awaited()

    async def test_ping_in_private_chat(self):
        await self.send_message("/ping")
        self.assert_reply(chat_id=42, text="pong")
        await self.assert_no_people_saved()

    async def test_ping_in_configured_group_and_supergroup(self):
        for chat_type in ("group", "supergroup"):
            with self.subTest(chat_type=chat_type):
                await self.send_message("/ping", chat_type=chat_type, chat_id=-100123)
                self.assert_reply(chat_id=-100123, text="pong")

    async def test_ping_preserves_forum_topic(self):
        for thread_id in (2, 456):
            with self.subTest(thread_id=thread_id):
                await self.send_message(
                    "/ping",
                    chat_type="supergroup",
                    chat_id=-100123,
                    thread_id=thread_id,
                    is_topic_message=True,
                )
                self.assert_reply(chat_id=-100123, text="pong", thread_id=thread_id)

    async def test_general_does_not_send_thread_identifier(self):
        for thread_id in (None, 1):
            with self.subTest(thread_id=thread_id):
                await self.send_message(
                    "/ping",
                    chat_type="supergroup",
                    chat_id=-100123,
                    thread_id=thread_id,
                    is_topic_message=False,
                )
                reply = self.assert_reply(chat_id=-100123, text="pong")
                self.assertNotIn(
                    "message_thread_id", reply.model_dump(exclude_none=True)
                )

    async def test_ping_in_other_groups_is_ignored_including_topics(self):
        for chat_type, thread_id in (("group", None), ("supergroup", None), ("supergroup", 456)):
            with self.subTest(chat_type=chat_type, thread_id=thread_id):
                await self.send_message(
                    "/ping",
                    chat_type=chat_type,
                    chat_id=-100999,
                    thread_id=thread_id,
                    is_topic_message=thread_id is not None,
                )
                self.request.assert_not_awaited()

    async def test_start_is_ignored_in_groups_including_configured_group(self):
        for chat_type in ("group", "supergroup"):
            for chat_id in (-100123, -100999):
                with self.subTest(chat_type=chat_type, chat_id=chat_id):
                    await self.send_message("/start", chat_type=chat_type, chat_id=chat_id)
                    self.request.assert_not_awaited()

    async def test_command_with_bot_mention_and_arguments(self):
        for command in ("/ping@EBLA_TEST_BOT", "/ping check"):
            with self.subTest(command=command):
                await self.send_message(command, chat_type="supergroup", chat_id=-100123)
                self.assert_reply(chat_id=-100123, text="pong")
        await self.send_message("/start@ebla_test_bot welcome")
        self.assertIn("/ping", self.assert_reply(chat_id=42).text)

    async def test_other_bots_commands_unknown_commands_and_plain_text_are_ignored(self):
        for text in (
            "/ping@another_bot", "/start@another_bot", "/unknown",
            "/pingpong", "/starter", "ping", "start", "hello", None,
        ):
            for chat_type, chat_id in (("private", 42), ("supergroup", -100123)):
                with self.subTest(text=text, chat_type=chat_type):
                    await self.send_message(text, chat_type=chat_type, chat_id=chat_id)
                    self.request.assert_not_awaited()

    async def test_channel_posts_and_edited_messages_do_not_trigger_commands(self):
        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=Chat(id=-100123, type="supergroup"),
            from_user=User(id=42, is_bot=False, first_name="Test"),
            text="/ping",
        )
        await self.dispatcher.feed_update(
            self.bot, Update(update_id=1, edited_message=message)
        )
        channel_post = message.model_copy(update={"chat": Chat(id=-100999, type="channel")})
        await self.dispatcher.feed_update(
            self.bot, Update(update_id=2, channel_post=channel_post)
        )
        self.request.assert_not_awaited()
