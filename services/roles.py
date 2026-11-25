from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.person import Person


class Role(StrEnum):
    ADMIN = "admin"
    MODERATOR = "moderator"
    USER = "user"


async def is_admin(session: AsyncSession, telegram_id: int) -> bool:
    result = await session.execute(
        select(Person.role).where(Person.telegram_id == telegram_id)
    )
    role = result.scalar_one_or_none()
    return role == Role.ADMIN

def role_for_registration(telegram_id: int, owner_id: int) -> Role | None:
    if telegram_id == owner_id:
        return Role.ADMIN
    return None