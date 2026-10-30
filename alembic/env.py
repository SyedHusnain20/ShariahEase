"""Alembic environment. Uses the app's own engine so the URL comes from DATABASE_URL / DATABASE_PATH."""
from alembic import context

from app.database import models  # noqa: F401  (registers every table on Base.metadata)
from app.database.db import Base, engine as app_engine

config = context.config
target_metadata = Base.metadata


def run_migrations_online() -> None:
    # Tests and app/database/migrate.py can hand in their own connection.
    connection = config.attributes.get("connection")
    if connection is None:
        with app_engine.connect() as connection:
            _run(connection)
    else:
        _run(connection)


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("Offline (SQL script) mode is not supported; run against a database.")
run_migrations_online()
