from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class Person(Base):
    __tablename__ = "people"

    telegram_id: Mapped[int] = mapped_column(primary_key=True)
    first_name: Mapped[str]
    last_name: Mapped[str | None]
    username: Mapped[str | None]
    # #13. Здесь добавь роль человека. Как описать три допустимых значения
    # и задать роль обычного пользователя для новых профилей?
