import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker

from config import Settings
from db.session import create_engine, create_tables
from handlers import create_router
from middlewares.database import DatabaseMiddleware
from services.lifecycle import LifecycleNotifications


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        settings = Settings()
    except ValidationError as error:
        raise SystemExit(f"Проверьте настройки .env:\n{error}") from None

    engine = create_engine(settings.database_path)
    try:
        await create_tables(engine)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        dispatcher = Dispatcher(settings=settings)
        dispatcher.update.outer_middleware(DatabaseMiddleware(session_factory))
        dispatcher.include_router(create_router())

        session = AiohttpSession(proxy=settings.telegram_proxy_url)
        notifications = LifecycleNotifications(
            chat_id=settings.status_chat_id, instance_name=settings.instance_name
        )
        session.middleware(notifications)
        dispatcher.shutdown.register(notifications.stopped)
        async with Bot(
            token=settings.bot_token.get_secret_value(), session=session
        ) as bot:
            await dispatcher.start_polling(
                bot,
                allowed_updates=[
                    "message",
                    "callback_query",
                    "chat_member",
                    "my_chat_member",
                ],
                close_bot_session=False,
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
