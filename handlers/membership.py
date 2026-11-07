import logging

from aiogram import Router
from aiogram.enums import ChatType
from aiogram.filters import Command, ChatMemberUpdatedFilter, IS_MEMBER, IS_NOT_MEMBER
from aiogram.types import ChatMemberUpdated
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from services.people import save_person

logger = logging.getLogger(__name__)


async def on_user_join(event: ChatMemberUpdated) -> None:
    pass

# Задача #11. Сначала добейся записи одного участника в тестовой группе.
#
# 1. Посмотри, как устроены роутеры в handlers/start.py и handlers/health.py.
#    Какой тип события нужен здесь? Найди в aiogram фильтр, который отличает
#    вступление в группу от выхода и смены роли.
#
# 2. Найди в событии ID группы и сравни его с настройкой проекта.
#    Кто окажется в событии, если одного человека добавил другой?
#    Проверь, из какого поля нужно брать профиль, и учти вход ботов.
#
# 3. Проследи, откуда /start получает сессию и где сохраняются изменения.
#    Используй тот же способ. Сможет ли участник с тем же ID сохраниться
#    повторно, если его username поменялся или исчез?
