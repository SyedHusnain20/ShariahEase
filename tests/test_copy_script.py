"""The one-time SQLite -> new database copy."""
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

SPEC = importlib.util.spec_from_file_location("copy_script", Path(__file__).resolve().parents[1] / "scripts" / "copy_sqlite_to_postgres.py")
copy_script = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(copy_script)

OLD_SCHEMA = [
    "CREATE TABLE users (id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL, email VARCHAR(150) NOT NULL, hashed_password VARCHAR(255) NOT NULL, language VARCHAR(10), created_at DATETIME)",
    "CREATE TABLE zakat_records (id INTEGER PRIMARY KEY, user_id INTEGER, user_name VARCHAR(100) NOT NULL, cash FLOAT, gold_value FLOAT, silver_value FLOAT, business_inventory FLOAT, receivables FLOAT, debts FLOAT, immediate_expenses FLOAT, total_assets FLOAT NOT NULL, total_deductions FLOAT NOT NULL, zakatable_wealth FLOAT NOT NULL, nisab_threshold FLOAT NOT NULL, zakat_due FLOAT NOT NULL, is_zakat_due BOOLEAN, nisab_rate_used VARCHAR(20), calculated_at DATETIME)",
    "CREATE TABLE screener_history (id INTEGER PRIMARY KEY, user_id INTEGER, query VARCHAR(300) NOT NULL, verdict VARCHAR(20) NOT NULL, explanation TEXT, screened_at DATETIME)",
    "CREATE TABLE chat_messages (id INTEGER PRIMARY KEY, user_id INTEGER, session_id VARCHAR(100) NOT NULL, role VARCHAR(20) NOT NULL, content TEXT NOT NULL, language VARCHAR(10), created_at DATETIME)",
]


@pytest.fixture
def old_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as c:
        for ddl in OLD_SCHEMA:
            c.execute(text(ddl))
        c.execute(text("INSERT INTO users (id,name,email,hashed_password,language,created_at) VALUES (5,'A','a@x.com','h','en','2026-01-01 00:00:00')"))
        c.execute(text("INSERT INTO users (id,name,email,hashed_password) VALUES (9,'B','b@x.com','h')"))
        c.execute(text("INSERT INTO chat_messages (id,user_id,session_id,role,content) VALUES (1,5,'s','user','hello'),(2,NULL,'g','user','guest'),(3,77,'s','user','orphan')"))
        c.execute(text("INSERT INTO screener_history (id,user_id,query,verdict) VALUES (1,9,'AAPL','halal')"))
        c.execute(text("INSERT INTO zakat_records (id,user_id,user_name,total_assets,total_deductions,zakatable_wealth,nisab_threshold,zakat_due) VALUES (1,5,'A',100,0,100,50,2.5)"))
    return engine


def test_copy_keeps_ids_counts_and_skips_guest_and_orphan_messages(old_db):
    target = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=__import__("sqlalchemy.pool").pool.StaticPool)
    from app.database.db import enable_sqlite_foreign_keys
    enable_sqlite_foreign_keys(target)
    counts = copy_script.copy_database(old_db, target)
    assert counts["users"]["copied"] == 2
    assert counts["chat_messages"] == {"copied": 1, "orphans_detached": 0, "skipped": 2}
    with target.connect() as c:
        assert [r[0] for r in c.execute(text("SELECT id FROM users ORDER BY id"))] == [5, 9]
        assert [r[0] for r in c.execute(text("SELECT id FROM chat_messages"))] == [1]      # guest (2) and orphan (3) not copied
        assert c.execute(text("SELECT timezone FROM users WHERE id = 5")).scalar() == "Asia/Karachi"
        assert c.execute(text("SELECT user_id FROM zakat_records")).scalar() == 5


def test_copy_refuses_a_target_that_already_has_users(old_db):
    from sqlalchemy.pool import StaticPool
    target = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    copy_script.copy_database(old_db, target)
    with pytest.raises(SystemExit):
        copy_script.copy_database(old_db, target)
    with target.connect() as c:
        assert c.execute(text("SELECT count(*) FROM users")).scalar() == 2      # unchanged


def test_normalise_url():
    assert copy_script.normalise_url("postgres://u@h/d") == "postgresql+psycopg://u@h/d"
    assert copy_script.normalise_url("postgresql://u@h/d") == "postgresql+psycopg://u@h/d"


@pytest.mark.pg
def test_pg_copy_resets_id_counters(old_db, pg_test_url):
    target = create_engine(pg_test_url)
    with target.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE")); c.execute(text("CREATE SCHEMA public"))
    copy_script.copy_database(old_db, target)
    with target.begin() as c:
        new_id = c.execute(text("INSERT INTO users (name,email,hashed_password) VALUES ('N','n@x.com','h') RETURNING id")).scalar()
    assert new_id == 10                                                          # continues after the highest copied id (9)
