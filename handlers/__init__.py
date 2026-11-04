from aiogram import Router

from handlers import health
from handlers import start


def create_router() -> Router:
    router = Router(name="main")
    router.include_router(health.create_router())
    
    router.include_router(start.create_router())

    return router
