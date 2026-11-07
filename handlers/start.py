from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from services.people import save_person


async def start(message: Message, session: AsyncSession) -> None:
    assert message.from_user is not None

    telegram_id = message.from_user.id
    first_name = message.from_user.first_name
    last_name = message.from_user.last_name
    username = message.from_user.username

    await save_person(session, telegram_id, first_name, last_name, username)

    await message.answer("Привет!\nЯ бот созданный персонально для группы Eblan4iki squad.\nПока не обещаю регистрацию, настройку профиля и другие будущие функции, но вы можете воспользоваться командой /ping для проверки доступности бота.")


def create_router() -> Router:
    router = Router(name="start")
    router.message.register(start, Command("start"), F.chat.type == ChatType.PRIVATE)

    return router