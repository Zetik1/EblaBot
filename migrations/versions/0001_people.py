"""Record the original people schema."""

import sqlalchemy as sa
from alembic import op

revision = "0001_people"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("people"):
        columns = {column["name"] for column in inspector.get_columns("people")}
        primary_key = inspector.get_pk_constraint("people")["constrained_columns"]
        if columns != {"telegram_id", "first_name", "last_name", "username"} or (
            primary_key != ["telegram_id"]
        ):
            raise RuntimeError("Таблица people не соответствует исходной схеме EblaBot")
        return

    op.create_table(
        "people",
        sa.Column("telegram_id", sa.Integer(), primary_key=True),
        sa.Column("first_name", sa.String(), nullable=False),
        sa.Column("last_name", sa.String()),
        sa.Column("username", sa.String()),
    )


def downgrade() -> None:
    raise RuntimeError("Начальная миграция сохраняет существующую таблицу people")
