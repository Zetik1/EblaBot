from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Person


async def save_person(
    session: AsyncSession,
    telegram_id: int,
    first_name: str,
    last_name: str | None,
    username: str | None,
) -> None:
    """Insert or update a profile and commit the supplied session."""
    insert_profile = sqlite_insert(Person).values(
        telegram_id=telegram_id,
        first_name=first_name,
        last_name=last_name,
        username=username,
    )
    upsert_profile = insert_profile.on_conflict_do_update(
        index_elements=[Person.telegram_id],
        set_={
            Person.first_name: insert_profile.excluded.first_name,
            Person.last_name: insert_profile.excluded.last_name,
            Person.username: insert_profile.excluded.username,
        },
    )
    await session.execute(upsert_profile)
    await session.commit()
