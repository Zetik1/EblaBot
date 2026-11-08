import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Person
from db.session import create_engine, create_tables
from services.people import save_person


class RoleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.engine = create_engine(Path(directory.name) / "test.sqlite3")
        self.addAsyncCleanup(self.engine.dispose)

    async def test_new_profile_gets_default_role(self) -> None:
        await create_tables(self.engine)
        async with AsyncSession(self.engine) as session:
            await save_person(session, 123, "Vlad", None, "vlad")

        async with AsyncSession(self.engine) as session:
            role = await session.scalar(select(Person.role))
            self.assertEqual(role, "user")

    async def test_database_rejects_unknown_role(self) -> None:
        await create_tables(self.engine)
        with self.assertRaisesRegex(IntegrityError, "ck_people_role"):
            async with self.engine.begin() as connection:
                await connection.exec_driver_sql(
                    "INSERT INTO people (telegram_id, first_name, role) VALUES (?, ?, ?)",
                    (123, "Vlad", "mod"),
                )

    async def test_concurrent_startups_apply_migrations_once(self) -> None:
        await asyncio.gather(create_tables(self.engine), create_tables(self.engine))
        async with self.engine.connect() as connection:
            revisions = await connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            )
            self.assertEqual(revisions.scalars().all(), ["0002_person_role"])

    async def test_old_profile_survives_migration_restart_and_profile_update(self) -> None:
        async with self.engine.begin() as connection:
            await connection.exec_driver_sql(
                """
                CREATE TABLE people (
                    telegram_id INTEGER NOT NULL PRIMARY KEY,
                    first_name VARCHAR NOT NULL,
                    last_name VARCHAR,
                    username VARCHAR
                )
                """
            )
            await connection.exec_driver_sql(
                "INSERT INTO people VALUES (?, ?, ?, ?)",
                (123, "Vlad", "Ivanov", "vlad"),
            )

        await create_tables(self.engine)
        async with AsyncSession(self.engine) as session:
            person = (await session.scalars(select(Person))).one()
            self.assertEqual(
                (
                    person.telegram_id,
                    person.first_name,
                    person.last_name,
                    person.username,
                    person.role,
                ),
                (123, "Vlad", "Ivanov", "vlad", "user"),
            )
            person.role = "moderator"
            await session.commit()

        await self.engine.dispose()
        await create_tables(self.engine)
        async with AsyncSession(self.engine) as session:
            await save_person(session, 123, "Vladimir", "Ivanov", "vladimir")

        async with AsyncSession(self.engine) as session:
            person = (await session.scalars(select(Person))).one()
            self.assertEqual(
                (person.first_name, person.username, person.role),
                ("Vladimir", "vladimir", "moderator"),
            )
