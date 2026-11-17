from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Person
from services.roles import Role
from services.roles import role_for_registration

logger = logging.getLogger(__name__)


async def save_person(
    session: AsyncSession,
    telegram_id: int,
    first_name: str,
    last_name: str | None,
    username: str | None,
    role: Role | None,
) -> None:
    """Insert or update a profile and commit the supplied session."""
    insert_profile = sqlite_insert(Person).values(
        telegram_id=telegram_id,
        first_name=first_name,
        last_name=last_name,
        username=username,
    )
    
    if role is not None:
        insert_profile = insert_profile.values(role=role)

    update_fields = {
            Person.first_name: insert_profile.excluded.first_name,
            Person.last_name: insert_profile.excluded.last_name,
            Person.username: insert_profile.excluded.username,
    }
    
    if role is not None:
        update_fields[Person.role] = insert_profile.excluded.role

    upsert_profile = insert_profile.on_conflict_do_update(
        index_elements=[Person.telegram_id],
        set_=update_fields,
    )
    await session.execute(upsert_profile)
    await session.commit()

async def register_person(
    session: AsyncSession,
    owner_id: int,
    telegram_id: int,
    first_name: str,
    last_name: str | None,
    username: str | None,
    
) -> None:
    role = role_for_registration(telegram_id, owner_id)
    try:
        await save_person(session, telegram_id, first_name, last_name, username, role)
    except SQLAlchemyError:
        logger.exception("Не удалось сохранить пользователя %s", telegram_id)