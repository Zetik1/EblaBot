import asyncio
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from db.models import Person
from db.session import create_engine, create_tables
from services.people import save_person


class PeopleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "test.sqlite3"
        self.engine = create_engine(self.path)
        self.addAsyncCleanup(self.engine.dispose)
        self.factory = async_sessionmaker(self.engine)
        await create_tables(self.engine)

    async def save(self, telegram_id, first_name, last_name, username):
        async with self.factory() as session:
            await save_person(session, telegram_id, first_name, last_name, username)

    async def read_people(self):
        async with self.factory() as session:
            result = await session.execute(
                select(Person.telegram_id, Person.first_name, Person.last_name, Person.username)
                .order_by(Person.telegram_id)
            )
            return result.all()

    async def test_saves_people_with_and_without_optional_fields(self):
        await self.save(123, "Гриша", "Иванов", "grisha")
        await self.save(2**40, "Влад", None, None)

        self.assertEqual(
            await self.read_people(),
            [(123, "Гриша", "Иванов", "grisha"), (2**40, "Влад", None, None)],
        )

    async def test_updates_existing_person_and_clears_removed_fields(self):
        await self.save(123, "Гриша", "Иванов", "grisha")
        await self.save(123, "Григорий", None, None)
        self.assertEqual(await self.read_people(), [(123, "Григорий", None, None)])

        await self.save(123, "Григорий", None, None)
        self.assertEqual(await self.read_people(), [(123, "Григорий", None, None)])

    async def test_person_remains_after_reopening_database(self):
        await self.save(123, "Гриша", "Иванов", "grisha")
        await self.save(123, "Григорий", None, None)
        await self.engine.dispose()

        reopened_engine = create_engine(self.path)
        self.addAsyncCleanup(reopened_engine.dispose)
        self.factory = async_sessionmaker(reopened_engine)
        await create_tables(reopened_engine)

        self.assertEqual(await self.read_people(), [(123, "Григорий", None, None)])

    async def test_concurrent_saves_keep_one_complete_record(self):
        first = (123, "Гриша", "Иванов", "grisha")
        second = (123, "Григорий", None, None)

        await asyncio.gather(self.save(*first), self.save(*second))

        people = await self.read_people()
        self.assertEqual(len(people), 1)
        self.assertIn(people[0], (first, second))
