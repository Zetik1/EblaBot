from aiogram import Router

from handlers import health
# Добавить import start


def create_router() -> Router:
    router = Router(name="main")
    router.include_router(health.create_router())
    
# Подключить роутер из модуля start

    return router
