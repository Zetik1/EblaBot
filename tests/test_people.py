import asyncio
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from db.models import Person
from db.session import create_engine, create_tables
from services.people import save_person


class PeopleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "test.sqlite3"
        self.engine = create_engine(self.path)
        self.addAsyncCleanup(self.engine.dispose)
        await create_tables(self.engine)

    async def _save_profile(
        self,
        telegram_id: int,
        first_name: str,
        last_name: str | None,
        username: str | None,
    ) -> None:
        async with AsyncSession(self.engine) as session:
            await save_person(session, telegram_id, first_name, last_name, username)

    async def _read_profiles(self, engine: AsyncEngine):
        profiles_by_id = select(
            Person.telegram_id,
            Person.first_name,
            Person.last_name,
            Person.username,
        ).order_by(Person.telegram_id)
        async with AsyncSession(engine) as session:
            result = await session.execute(profiles_by_id)
            return result.all()

    async def test_saves_people_with_and_without_optional_fields(self) -> None:
        await self._save_profile(123, "Гриша", "Иванов", "grisha")
        await self._save_profile(2**40, "Влад", None, None)

        self.assertEqual(
            await self._read_profiles(self.engine),
            [(123, "Гриша", "Иванов", "grisha"), (2**40, "Влад", None, None)],
        )

    async def test_repeated_saves_update_and_clear_profile_fields(self) -> None:
        await self._save_profile(123, "Гриша", "Иванов", "grisha")
        await self._save_profile(123, "Григорий", "Петров", "grigory")
        self.assertEqual(
            await self._read_profiles(self.engine),
            [(123, "Григорий", "Петров", "grigory")],
        )

        await self._save_profile(123, "Григорий", None, None)
        self.assertEqual(
            await self._read_profiles(self.engine), [(123, "Григорий", None, None)]
        )

        await self._save_profile(123, "Григорий", None, None)
        self.assertEqual(
            await self._read_profiles(self.engine), [(123, "Григорий", None, None)]
        )

    async def test_person_remains_after_reopening_database(self) -> None:
        await self._save_profile(123, "Гриша", "Иванов", "grisha")
        await self._save_profile(123, "Григорий", None, None)
        await self.engine.dispose()

        reopened_engine = create_engine(self.path)
        self.addAsyncCleanup(reopened_engine.dispose)
        self.assertEqual(
            await self._read_profiles(reopened_engine), [(123, "Григорий", None, None)]
        )

    async def test_concurrent_saves_keep_one_complete_record(self) -> None:
        first_profile = (123, "Гриша", "Иванов", "grisha")
        second_profile = (123, "Григорий", None, None)

        await asyncio.gather(
            self._save_profile(*first_profile),
            self._save_profile(*second_profile),
        )

        profiles = await self._read_profiles(self.engine)
        self.assertEqual(len(profiles), 1)
        self.assertIn(profiles[0], (first_profile, second_profile))
