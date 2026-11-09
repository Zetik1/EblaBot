from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message

from config import Settings


async def ping(message: Message) -> None:
    await message.answer("pong")

async def ping_in_group(message: Message, settings: Settings) -> None:
    perm_gid = settings.group_id
    if message.chat.id != perm_gid:
        return
    await message.answer("pong")

def create_router() -> Router:
    router = Router(name="health")
    router.message.register(ping, Command("ping"), F.chat.type == ChatType.PRIVATE)
    router.message.register(ping_in_group, Command("ping"), F.chat.type != ChatType.PRIVATE)
    
    return router 
