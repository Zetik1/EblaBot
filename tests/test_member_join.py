import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiogram import Bot, Dispatcher
from aiogram.types import (
    Chat,
    ChatMemberAdministrator,
    ChatMemberBanned,
    ChatMemberLeft,
    ChatMemberMember,
    ChatMemberRestricted,
    ChatMemberUpdated,
    Update,
    User,
)
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from config import Settings
from db.models import Person
from db.session import create_engine, create_tables
from handlers import create_router
from middlewares.database import DatabaseMiddleware

GROUP_ID = -100123
OTHER_GROUP_ID = -100999


def administrator(user):
    # Обязательные поля администратора зависят от версии aiogram, поэтому заполняем их сами.
    flags = {
        name: True
        for name, field in ChatMemberAdministrator.model_fields.items()
        if field.is_required() and name != "user"
    }
    return ChatMemberAdministrator(user=user, **flags)


def restricted(user, *, is_member):
    # is_member=True: человек с ограничениями всё ещё в группе, False: его в группе нет.
    flags = {
        name: False
        for name, field in ChatMemberRestricted.model_fields.items()
        if field.is_required() and name not in ("user", "is_member", "until_date")
    }
    return ChatMemberRestricted(user=user, is_member=is_member, until_date=0, **flags)


class MemberJoinTests(unittest.IsolatedAsyncioTestCase):
    """chat_member -> Dispatcher -> on_member_join -> настоящая SQLite -> чтение новой сессией."""

    async def asyncSetUp(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(
                _env_file=None,
                bot_token="123456:local-test-token",
                owner_id=1,
                group_id=GROUP_ID,
            )
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.engine = create_engine(Path(directory.name) / "members.sqlite3")
        self.addAsyncCleanup(self.engine.dispose)
        await create_tables(self.engine)

        self.dispatcher = Dispatcher(settings=settings)
        self.dispatcher.update.outer_middleware(
            DatabaseMiddleware(async_sessionmaker(self.engine, expire_on_commit=False))
        )
        self.dispatcher.include_router(create_router())
        self.bot = Bot(token=settings.bot_token.get_secret_value())
        self.addAsyncCleanup(self.bot.session.close)
        self.request = AsyncMock()
        request_patch = patch.object(self.bot.session, "make_request", self.request)
        request_patch.start()
        self.addCleanup(request_patch.stop)
        self.update_id = 0

    async def send_member_update(
        self,
        old_status,
        new_status,
        *,
        user_id=7,
        first_name="Гриша",
        last_name=None,
        username=None,
        is_bot=False,
        chat_id=GROUP_ID,
        inviter_id=42,
    ):
        """old_status/new_status: left, member, administrator, kicked,
        restricted_in (is_member=True) или restricted_out (is_member=False)."""
        user = User(
            id=user_id,
            is_bot=is_bot,
            first_name=first_name,
            last_name=last_name,
            username=username,
        )
        statuses = {
            "left": lambda: ChatMemberLeft(user=user),
            "member": lambda: ChatMemberMember(user=user),
            "administrator": lambda: administrator(user),
            "kicked": lambda: ChatMemberBanned(user=user, until_date=0),
            "restricted_in": lambda: restricted(user, is_member=True),
            "restricted_out": lambda: restricted(user, is_member=False),
        }
        self.update_id += 1
        event = ChatMemberUpdated(
            chat=Chat(id=chat_id, type="supergroup"),
            # тот, кто добавил, чтобы тесты ловили путаницу с new_chat_member.user
            from_user=User(id=inviter_id, is_bot=False, first_name="Пригласивший"),
            date=datetime.now(timezone.utc),
            old_chat_member=statuses[old_status](),
            new_chat_member=statuses[new_status](),
        )
        await self.dispatcher.feed_update(
            self.bot, Update(update_id=self.update_id, chat_member=event)
        )

    async def read_people(self):
        # Новая сессия: видно только то, что реально записано и закоммичено в файл.
        profiles_by_id = select(
            Person.telegram_id,
            Person.first_name,
            Person.last_name,
            Person.username,
        ).order_by(Person.telegram_id)
        async with AsyncSession(self.engine) as session:
            result = await session.execute(profiles_by_id)
            return result.all()

    async def test_join_to_configured_group_creates_person(self):
        await self.send_member_update(
            "left", "member", first_name="Гриша", last_name="Иванов", username="grisha"
        )

        # сохранён вошедший (7), а не тот, кто его добавил (42)
        self.assertEqual(await self.read_people(), [(7, "Гриша", "Иванов", "grisha")])
        self.request.assert_not_awaited()

    async def test_join_without_optional_fields_stores_null(self):
        await self.send_member_update("left", "member", first_name="Анна")

        self.assertEqual(await self.read_people(), [(7, "Анна", None, None)])

    async def test_rejoin_with_changed_data_updates_same_record(self):
        await self.send_member_update(
            "left", "member", first_name="Гриша", last_name="Иванов", username="grisha"
        )
        await self.send_member_update("member", "left", first_name="Гриша")
        await self.send_member_update(
            "left", "member", first_name="Григорий", last_name="Петров", username="grigory"
        )
        self.assertEqual(
            await self.read_people(), [(7, "Григорий", "Петров", "grigory")]
        )

        await self.send_member_update("member", "left", first_name="Григорий")
        await self.send_member_update("left", "member", first_name="Григорий")
        self.assertEqual(await self.read_people(), [(7, "Григорий", None, None)])

    async def test_join_from_banned_state_also_creates_person(self):
        await self.send_member_update("kicked", "member", first_name="Олег")

        self.assertEqual(await self.read_people(), [(7, "Олег", None, None)])

    async def test_bot_join_does_not_change_table(self):
        await self.send_member_update("left", "member", user_id=555, is_bot=True)

        self.assertEqual(await self.read_people(), [])
        self.request.assert_not_awaited()

    async def test_join_to_other_group_does_not_change_table(self):
        await self.send_member_update("left", "member", chat_id=OTHER_GROUP_ID)

        self.assertEqual(await self.read_people(), [])
        self.request.assert_not_awaited()

    async def test_leave_and_kick_do_not_change_table(self):
        # на пустой таблице записи не появляются
        await self.send_member_update("member", "left")
        await self.send_member_update("member", "kicked")
        self.assertEqual(await self.read_people(), [])

        # у существующей записи данные не обновляются, даже если в событии они новые
        await self.send_member_update("left", "member", first_name="Гриша", username="grisha")
        await self.send_member_update("member", "left", first_name="Новое", username="new")
        await self.send_member_update("member", "kicked", first_name="Новое", username="new")
        self.assertEqual(await self.read_people(), [(7, "Гриша", None, "grisha")])

    async def test_role_change_of_active_member_does_not_change_table(self):
        # на пустой таблице записи не появляются
        await self.send_member_update("member", "administrator")
        self.assertEqual(await self.read_people(), [])

        # у существующей записи данные не обновляются, даже если в событии они новые
        await self.send_member_update("left", "member", first_name="Гриша", username="grisha")
        await self.send_member_update(
            "member", "administrator", first_name="Новое", username="new"
        )
        await self.send_member_update(
            "administrator", "member", first_name="Другое", username="other"
        )
        self.assertEqual(await self.read_people(), [(7, "Гриша", None, "grisha")])

    async def test_restriction_of_active_member_does_not_change_table(self):
        # member -> restricted с is_member=True: человек всё ещё в группе
        await self.send_member_update("member", "restricted_in")
        self.assertEqual(await self.read_people(), [])

        await self.send_member_update("left", "member", first_name="Гриша", username="grisha")
        await self.send_member_update(
            "member", "restricted_in", first_name="Новое", username="new"
        )
        await self.send_member_update(
            "restricted_in", "member", first_name="Другое", username="other"
        )
        self.assertEqual(await self.read_people(), [(7, "Гриша", None, "grisha")])

    async def test_restricted_user_who_is_not_in_group_joins_as_member(self):
        # restricted с is_member=False -> member: человек вошёл
        await self.send_member_update(
            "restricted_out", "member", first_name="Олег", username="oleg"
        )

        self.assertEqual(await self.read_people(), [(7, "Олег", None, "oleg")])

    async def test_restricted_user_who_is_not_in_group_joins_still_restricted(self):
        # restricted с is_member=False -> restricted с is_member=True: тоже вход
        await self.send_member_update("restricted_out", "restricted_in", first_name="Анна")

        self.assertEqual(await self.read_people(), [(7, "Анна", None, None)])

    async def test_restricted_user_leaving_does_not_change_table(self):
        # member -> restricted с is_member=False: человек вышел или был исключён
        await self.send_member_update("left", "member", first_name="Гриша", username="grisha")
        await self.send_member_update(
            "member", "restricted_out", first_name="Новое", username="new"
        )
        self.assertEqual(await self.read_people(), [(7, "Гриша", None, "grisha")])

    async def test_database_failure_is_logged_and_nothing_is_sent(self):
        # Ломаем настоящую БД: save_person упадёт реальной ошибкой SQLAlchemy.
        async with self.engine.begin() as connection:
            await connection.execute(text(f'DROP TABLE "{Person.__tablename__}"'))

        with self.assertLogs(level="ERROR") as logs:
            await self.send_member_update("left", "member", user_id=7)

        self.assertIn("id: 7", logs.output[0])
        self.request.assert_not_awaited()