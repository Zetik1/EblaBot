from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Person


async def save_person(
    session: AsyncSession,
    telegram_id: int,
    first_name: str,
    last_name: str | None,
    username: str | None,
) -> None:
    """Save a Telegram profile and commit the supplied session."""
    statement = insert(Person).values(
        telegram_id=telegram_id,
        first_name=first_name,
        last_name=last_name,
        username=username,
    )
    # SQLite resolves duplicate IDs in the same statement as the insert.
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[Person.telegram_id],
            set_={
                "first_name": statement.excluded.first_name,
                "last_name": statement.excluded.last_name,
                "username": statement.excluded.username,
            },
        )
    )
    await session.commit()
