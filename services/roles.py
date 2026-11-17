from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    MODERATOR = "moderator"
    USER = "user"


def role_for_registration(telegram_id: int, owner_id: int) -> Role | None:
    if telegram_id == owner_id:
        return Role.ADMIN
    return None