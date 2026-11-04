from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message

async def start(message: Message) -> None:
    name = message.from_user.first_name
    await message.answer(f"Привет, {name}!\nЯ бот созданный персонально для группы Eblan4iki squad.\nПока не обещаю регистрацию, настройку профиля и другие будущие функции, но вы можете воспользоваться командой /ping для проверки доступности бота.")

def create_router() -> Router:
    router = Router(name="start")
    router.message.register(start, Command("start"), F.chat.type == ChatType.PRIVATE)

    return router