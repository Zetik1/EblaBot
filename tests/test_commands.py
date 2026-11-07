import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiogram import Bot, Dispatcher
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, Update, User
from sqlalchemy.ext.asyncio import async_sessionmaker

from config import Settings
from db.session import create_engine, create_tables
from handlers import create_router
from middlewares.database import DatabaseMiddleware


class CommandTests(unittest.IsolatedAsyncioTestCase):
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
        chat_type="private",
        chat_id=42,
        thread_id=None,
        is_topic_message=None,
        first_name="Влад",
    ):
        self.request.reset_mock()
        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=Chat(id=chat_id, type=chat_type),
            from_user=User(id=42, is_bot=False, first_name=first_name),
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

    async def test_start_greets_user_and_mentions_ping_in_private_chat(self):
        for name in ("Влад", "Гриша"):
            with self.subTest(name=name):
                await self.send_message("/start", first_name=name)
                reply = self.assert_reply(chat_id=42)
                self.assertIn(f"Привет, {name}!", reply.text)
                self.assertIn("/ping", reply.text)

    async def test_ping_in_private_chat(self):
        await self.send_message("/ping")
        self.assert_reply(chat_id=42, text="pong")

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
