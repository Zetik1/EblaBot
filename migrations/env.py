from pathlib import Path

from alembic import context
from sqlalchemy import URL, create_engine

from db.models import Base


def get_database_url() -> URL:
    database_url = context.config.attributes.get("database_url")
    if database_url is not None:
        return database_url

    database_path = context.get_x_argument(as_dictionary=True).get("db")
    if not database_path:
        raise SystemExit("Укажите путь к SQLite через -x db=путь")
    return URL.create("sqlite", database=str(Path(database_path).resolve()))


def run_migrations() -> None:
    engine = create_engine(get_database_url())
    try:
        with engine.begin() as connection:
            # Open a real SQLite transaction before reading the revision or executing DDL.
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            context.configure(connection=connection, target_metadata=Base.metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


run_migrations()
