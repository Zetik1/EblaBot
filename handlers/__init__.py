from aiogram import Router

from handlers import health


def create_router() -> Router:
    router = Router(name="main")
    router.include_router(health.create_router())
    return router
