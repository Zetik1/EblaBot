from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message


async def ping(message: Message) -> None:
    await message.answer("pong")

# Тут добавить отдельный обработчик /ping для группы
# Настройкир получить аргументом settings, разрешенную группу взять из settings.group_id
# Если команда пришла из другой группы -- ничего не отправлять
# Ответ pong должен остаться в той же теме
# Для General отправлять без message_thread_id, не передавать значение 1

def create_router() -> Router:
    router = Router(name="health")
    router.message.register(ping, Command("ping"), F.chat.type == ChatType.PRIVATE)
    
    # Здесь зарегистрировать новый обработчик для групп и супергрупп
    # Существующий /ping в личке должен продолжать работать
    
    return router
