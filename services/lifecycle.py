import asyncio
import logging
from uuid import uuid4

from aiogram import Bot
from aiogram.client.session.middlewares.base import (
    BaseRequestMiddleware,
    NextRequestMiddlewareType,
)
from aiogram.exceptions import TelegramAPIError, TelegramConflictError
from aiogram.methods import GetUpdates, TelegramMethod
from aiogram.methods.base import TelegramType

logger = logging.getLogger(__name__)


class LifecycleNotifications(BaseRequestMiddleware):
    def __init__(self, *, chat_id: int | None, instance_name: str) -> None:
        self.chat_id = chat_id
        self.label = f"{instance_name} · {uuid4().hex[:8]}"
        self.started = False
        self.conflict_seen = False
        self.polling_tasks: set[asyncio.Task] = set()

    async def send(self, bot: Bot, text: str) -> None:
        if self.chat_id is None:
            return
        try:
            async with asyncio.timeout(5):
                await bot.send_message(
                    chat_id=self.chat_id,
                    text=f"{text}\nКопия: {self.label}",
                    parse_mode=None,
                )
        except (TelegramAPIError, TimeoutError) as error:
            logger.warning("Не удалось отправить статус бота: %s", type(error).__name__)

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> TelegramType:
        if not isinstance(method, GetUpdates):
            return await make_request(bot, method)
        polling_task = asyncio.current_task()
        if polling_task is not None and polling_task not in self.polling_tasks:
            self.polling_tasks.add(polling_task)
            polling_task.add_done_callback(self.polling_tasks.discard)
        try:
            result = await make_request(bot, method)
        except TelegramConflictError:
            if not self.conflict_seen:
                self.conflict_seen = True
                await self.send(
                    bot,
                    "⚠️ Конфликт получения сообщений. Проверьте, не запущена ли "
                    "ещё одна копия с этим токеном, и договоритесь, кто остаётся. "
                    "Если копия одна — проверьте webhook.",
                )
            raise
        if not self.started:
            self.started = True
            await self.send(
                bot,
                "🟢 Бот запущен и получает сообщения. Токен используется — "
                "перед запуском другой копии договоритесь об остановке этой.",
            )
        return result

    async def stopped(self, bot: Bot) -> None:
        current_task = asyncio.current_task()
        pending = [
            task for task in self.polling_tasks
            if not task.done() and task is not current_task
        ]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if not self.started and not self.conflict_seen:
            return
        await self.send(
            bot,
            "⚪ Эта копия бота прекратила получать сообщения. "
            "Перед передачей токена убедитесь, что других работающих копий нет.",
        )
