import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from services.roles import is_admin

logger = logging.getLogger(__name__)


async def admincheck(message: Message, session: AsyncSession) -> None:
    assert message.from_user is not None
    telegram_id = message.from_user.id

    try:
        if await is_admin(session, telegram_id):
            await message.answer("Вам доступны права администратора.")
        else:
            await message.answer("Данная команда доступна только администратору.")
    except SQLAlchemyError:
        logger.exception("Не удалось проверить роль пользователя, %s", telegram_id)
        await message.answer(
            "Не удалось осуществить проверку ваших прав, пожалуйста, обратитесь к главному администратору!"
        )
        return

def create_router() -> Router:
    router = Router(name="admincheck")
    router.message.register(admincheck, Command("admincheck"), F.chat.type == ChatType.PRIVATE)
    
    return router