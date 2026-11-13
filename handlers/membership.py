import logging

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import ChatMemberUpdatedFilter, IS_MEMBER, IS_NOT_MEMBER
from aiogram.types import ChatMemberUpdated
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from config import Settings
from services.people import save_person

logger = logging.getLogger(__name__)


async def on_member_join(event: ChatMemberUpdated, session: AsyncSession, settings: Settings) -> None:
    user = event.new_chat_member.user

    if event.chat.id != settings.group_id:
        return
    if user.is_bot:
        return

    greeting_text = f"Новость планетарного масштаба!\nВ чатике ебланчиков случилось пополнение, поэтому живо все побежали здороваться с {user.first_name}"

    try:
        await save_person(session, 
            user.id,
            user.first_name,
            user.last_name,
            user.username
        )
    except SQLAlchemyError:
        logger.exception("Не удалось сохранить пользователя по событию new_chat_member, id: %s", user.id)
        return

    try:
        await event.answer(greeting_text, message_thread_id=settings.welcome_theme_id)
    except TelegramAPIError:
        logger.exception("Не удалось отправить приветственное сообщение новому участнику группы, id: %s", user.id)


def create_router() -> Router:
    router = Router(name="on_member_join")
    router.chat_member.register(on_member_join, ChatMemberUpdatedFilter(IS_NOT_MEMBER >> IS_MEMBER))
    return router