import asyncio
import unittest
from unittest.mock import AsyncMock

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramConflictError, TelegramForbiddenError
from aiogram.methods import GetUpdates, SendMessage
from aiogram.types import User

from services.lifecycle import LifecycleNotifications


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_polling_sends_start_then_stop_before_session_closes(self):
        await self.check_polling_shutdown(cancel=False)

    async def test_cancellation_stops_polling_before_notification(self):
        await self.check_polling_shutdown(cancel=True)

    async def check_polling_shutdown(self, *, cancel):
        notifications = LifecycleNotifications(chat_id=-123, instance_name="Гриша")
        dispatcher = Dispatcher()
        dispatcher.shutdown.register(notifications.stopped)
        bot = Bot(token="123456:local-test-token")
        bot._me = User(id=123456, is_bot=True, first_name="Test", username="test_bot")
        sent = []
        polling_started = asyncio.Event()

        async def request(_bot, method, timeout=None):
            if isinstance(method, GetUpdates):
                if not notifications.started:
                    return []
                polling_started.set()
                await asyncio.Event().wait()
            if isinstance(method, SendMessage):
                sent.append(method)
                return True
            self.fail(f"Unexpected method: {type(method).__name__}")

        bot.session.make_request = request
        bot.session.middleware(notifications)
        try:
            task = asyncio.create_task(
                dispatcher.start_polling(bot, handle_signals=False, close_bot_session=False)
            )
            await asyncio.wait_for(polling_started.wait(), timeout=2)
            if cancel:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            else:
                await dispatcher.stop_polling()
                await asyncio.wait_for(task, timeout=2)
            self.assertFalse(any(not task.done() for task in notifications.polling_tasks))
            self.assertEqual(len(sent), 2)
            self.assertIn("Бот запущен", sent[0].text)
            self.assertIn("прекратила получать", sent[1].text)
            self.assertTrue(all(item.chat_id == -123 for item in sent))
            self.assertTrue(all(notifications.label in item.text for item in sent))
        finally:
            await bot.session.close()

    async def test_conflict_is_reported_once_and_propagated(self):
        notifications = LifecycleNotifications(chat_id=-123, instance_name="Влад")
        bot = AsyncMock()
        method = GetUpdates()
        request = AsyncMock(side_effect=TelegramConflictError(method=method, message="busy"))
        for _ in range(2):
            with self.assertRaises(TelegramConflictError):
                await notifications(request, bot, method)
        self.assertFalse(notifications.started)
        bot.send_message.assert_awaited_once()
        self.assertIn("Конфликт", bot.send_message.call_args.kwargs["text"])

    async def test_failed_delivery_does_not_break_polling_or_shutdown(self):
        notifications = LifecycleNotifications(chat_id=-123, instance_name="Влад")
        method = SendMessage(chat_id=-123, text="test")
        bot = AsyncMock()
        bot.send_message.side_effect = TelegramForbiddenError(method=method, message="secret")
        with self.assertLogs("services.lifecycle", level="WARNING") as logs:
            result = await notifications(AsyncMock(return_value=[]), bot, GetUpdates())
            await notifications.stopped(bot)
        self.assertEqual(result, [])
        self.assertNotIn("secret", " ".join(logs.output))
        self.assertEqual(bot.send_message.await_count, 2)

    async def test_disabled_notifications_and_no_false_stop_before_start(self):
        bot = AsyncMock()
        notifications = LifecycleNotifications(chat_id=None, instance_name="Влад")
        await notifications(AsyncMock(return_value=[]), bot, GetUpdates())
        await notifications.stopped(bot)
        fresh = LifecycleNotifications(chat_id=-123, instance_name="Гриша")
        await fresh.stopped(bot)
        bot.send_message.assert_not_awaited()

    async def test_timeout_is_logged_without_interrupting_shutdown(self):
        notifications = LifecycleNotifications(chat_id=-123, instance_name="Гриша")
        notifications.started = True
        bot = AsyncMock()
        bot.send_message.side_effect = TimeoutError
        with self.assertLogs("services.lifecycle", level="WARNING"):
            await notifications.stopped(bot)
