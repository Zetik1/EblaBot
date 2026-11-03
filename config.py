from pathlib import Path

from aiogram.utils.token import TokenValidationError, validate_token
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    telegram_proxy_url: str | None = None
    bot_token: SecretStr
    owner_id: int = Field(gt=0)
    group_id: int = Field(lt=0)
    database_path: Path = Path("data/bot.sqlite3")

    @field_validator("bot_token")
    @classmethod
    def validate_bot_token(cls, value: SecretStr) -> SecretStr:
        try:
            validate_token(value.get_secret_value())
        except TokenValidationError:
            raise ValueError("Укажите токен бота из BotFather") from None
        return value

    @field_validator("database_path")
    @classmethod
    def resolve_database_path(cls, value: Path) -> Path:
        if value.is_absolute():
            return value
        return PROJECT_ROOT / value
