"""
Run Alembic migrations from inside the app (and from the command line).

    python -m app.database.migrate          # upgrade to the latest revision

Databases created before migrations existed (tables from the old
Base.metadata.create_all, no alembic_version table) are stamped with the
baseline revision first, so the first upgrade only adds what is new.
"""
import logging
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

logger = logging.getLogger(__name__)

BASELINE_REVISION = "0001"
_ROOT = Path(__file__).resolve().parents[2]


def _config(connection) -> Config:
    cfg = Config(str(_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_ROOT / "alembic"))
    cfg.attributes["connection"] = connection
    return cfg


def upgrade_to_head(engine=None) -> None:
    """Bring the database up to date. Safe to call on every start."""
    if engine is None:
        from app.database.db import engine as default_engine
        engine = default_engine

    with engine.begin() as connection:
        names = set(inspect(connection).get_table_names())
        cfg = _config(connection)
        if "users" in names and "alembic_version" not in names:
            logger.info("Existing database without migration history: stamping baseline %s", BASELINE_REVISION)
            command.stamp(cfg, BASELINE_REVISION)
        command.upgrade(cfg, "head")


def should_auto_migrate(env=None) -> bool:
    env = os.environ if env is None else env
    return (env.get("AUTO_MIGRATE", "true").strip().lower() not in {"0", "false", "no", "off"})


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    upgrade_to_head()
    print("Database is up to date.")
