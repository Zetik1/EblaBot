from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message


async def ping(message: Message) -> None:
    await message.answer("pong")


def create_router() -> Router:
    router = Router(name="health")
    router.message.register(ping, Command("ping"), F.chat.type == ChatType.PRIVATE)
    return router
