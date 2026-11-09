from sqlalchemy import CheckConstraint, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class Person(Base):
    __tablename__ = "people"

    telegram_id: Mapped[int] = mapped_column(primary_key=True)
    first_name: Mapped[str]
    last_name: Mapped[str | None]
    username: Mapped[str | None]
    role: Mapped[str] = mapped_column(
        Text,
        CheckConstraint("role IN ('user', 'moderator', 'admin')", name="ck_people_role"),
        server_default="user",
    )
