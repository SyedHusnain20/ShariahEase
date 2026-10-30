"""Alembic migrations: fresh database, legacy database, and (with a real Postgres) the full cycle."""
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool

from app.database.migrate import upgrade_to_head, should_auto_migrate

PHASE0_TABLES = {"refresh_tokens", "webhook_events", "maintenance_runs"}
LEGACY_TABLES = {"users", "zakat_records", "screener_history", "chat_messages"}


def test_fresh_sqlite_gets_every_table(db_engine):
    names = set(inspect(db_engine).get_table_names())
    assert LEGACY_TABLES | PHASE0_TABLES | {"alembic_version"} <= names
    cols = {c["name"] for c in inspect(db_engine).get_columns("users")}
    assert {"timezone", "token_version", "is_admin", "email_verified_at", "privacy_accepted_at"} <= cols


def test_upgrade_is_idempotent(db_engine):
    upgrade_to_head(db_engine)
    upgrade_to_head(db_engine)
    with db_engine.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0003"


def test_legacy_database_is_stamped_and_keeps_its_data():
    """A database made by the old create_all (no alembic_version) must upgrade without losing rows."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with engine.begin() as c:
        c.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL, email VARCHAR(150) NOT NULL, "
                       "hashed_password VARCHAR(255) NOT NULL, language VARCHAR(10), created_at DATETIME)"))
        c.execute(text("CREATE UNIQUE INDEX ix_users_email ON users (email)"))
        c.execute(text("CREATE TABLE chat_messages (id INTEGER PRIMARY KEY, user_id INTEGER, session_id VARCHAR(100) NOT NULL, "
                       "role VARCHAR(20) NOT NULL, content TEXT NOT NULL, language VARCHAR(10), created_at DATETIME)"))
        c.execute(text("INSERT INTO users (id, name, email, hashed_password) VALUES (1, 'Old', 'old@x.com', 'h')"))
    upgrade_to_head(engine)
    with engine.connect() as c:
        row = c.execute(text("SELECT name, timezone, token_version, is_admin FROM users WHERE id = 1")).one()
        assert tuple(row) == ("Old", "Asia/Karachi", 0, 0)
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0003"


def test_migration_0003_deletes_guest_messages_only():
    from alembic import command
    from app.database.migrate import _config

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with engine.begin() as c:
        command.upgrade(_config(c), "0002")
        c.execute(text("INSERT INTO users (id,name,email,hashed_password) VALUES (1,'A','a@x.com','h')"))
        c.execute(text("INSERT INTO chat_messages (user_id,session_id,role,content) VALUES (1,'s','user','kept'),(NULL,'g','user','guest 1'),(NULL,'g','assistant','guest 2')"))
    upgrade_to_head(engine)
    with engine.connect() as c:
        assert [r[0] for r in c.execute(text("SELECT content FROM chat_messages"))] == ["kept"]


@pytest.mark.parametrize("value,expected", [
    (None, True), ("true", True), ("1", True), ("false", False), ("FALSE", False), ("0", False), ("off", False), ("no", False),
])
def test_auto_migrate_switch(value, expected):
    env = {} if value is None else {"AUTO_MIGRATE": value}
    assert should_auto_migrate(env) is expected


# ── real PostgreSQL (opt in: python -m pytest -m pg, with DATABASE_URL_TEST set) ──

@pytest.fixture
def pg_engine(pg_test_url):
    engine = create_engine(pg_test_url)
    with engine.begin() as c:                      # throwaway database: start from nothing
        c.execute(text("DROP SCHEMA public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
    yield engine
    engine.dispose()


@pytest.mark.pg
def test_pg_upgrade_matches_models_and_cascades(pg_engine):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from app.database import models  # noqa: F401
    from app.database.db import Base

    upgrade_to_head(pg_engine)
    with pg_engine.connect() as c:
        drift = compare_metadata(MigrationContext.configure(c, opts={"compare_type": True}), Base.metadata)
    assert drift == []

    with pg_engine.begin() as c:
        c.execute(text("INSERT INTO users (name,email,hashed_password) VALUES ('A','a@x.com','h')"))
        uid = c.execute(text("SELECT id FROM users")).scalar()
        c.execute(text("INSERT INTO chat_messages (user_id,session_id,role,content) VALUES (:u,'s','user','hi')"), {"u": uid})
        c.execute(text("INSERT INTO zakat_records (user_id,user_name,total_assets,total_deductions,zakatable_wealth,nisab_threshold,zakat_due) "
                       "VALUES (:u,'A',1,0,1,1,0)"), {"u": uid})
        c.execute(text("INSERT INTO refresh_tokens (user_id,family_id,token_hash,expires_at) "
                       "VALUES (:u, gen_random_uuid(), repeat('a',64), now() + interval '1 day')"), {"u": uid})
    with pg_engine.begin() as c:
        c.execute(text("DELETE FROM users"))
    with pg_engine.connect() as c:
        for t in ("chat_messages", "zakat_records", "refresh_tokens"):
            assert c.execute(text(f"SELECT count(*) FROM {t}")).scalar() == 0, t


@pytest.mark.pg
def test_pg_downgrade_and_upgrade_again(pg_engine):
    from alembic import command
    from app.database.migrate import _config

    upgrade_to_head(pg_engine)
    with pg_engine.begin() as c:
        command.downgrade(_config(c), "0001")
    names = set(inspect(pg_engine).get_table_names())
    assert not (PHASE0_TABLES & names)
    assert "timezone" not in {c["name"] for c in inspect(pg_engine).get_columns("users")}
    upgrade_to_head(pg_engine)
    assert PHASE0_TABLES <= set(inspect(pg_engine).get_table_names())
