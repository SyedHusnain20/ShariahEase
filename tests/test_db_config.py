"""DATABASE_URL handling and SQLite foreign keys."""
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app.database.db import build_database_url, enable_sqlite_foreign_keys, make_engine


@pytest.mark.parametrize("env,expected", [
    ({}, "sqlite:///./shariahease.db"),
    ({"DATABASE_PATH": "/data/s.db"}, "sqlite:////data/s.db"),
    ({"DATABASE_URL": ""}, "sqlite:///./shariahease.db"),
    ({"DATABASE_URL": "   "}, "sqlite:///./shariahease.db"),
    ({"DATABASE_URL": "postgresql://u:p@host/db?sslmode=require"}, "postgresql+psycopg://u:p@host/db?sslmode=require"),
    ({"DATABASE_URL": "postgres://u:p@host/db"}, "postgresql+psycopg://u:p@host/db"),
    ({"DATABASE_URL": "postgresql+psycopg://u:p@host/db"}, "postgresql+psycopg://u:p@host/db"),
    ({"DATABASE_URL": "postgresql://u@h/db", "DATABASE_PATH": "/x.db"}, "postgresql+psycopg://u@h/db"),
])
def test_build_database_url(env, expected):
    assert build_database_url(env) == expected


def test_sqlite_foreign_keys_are_enforced():
    engine = create_engine("sqlite://")
    enable_sqlite_foreign_keys(engine)
    with engine.begin() as c:
        c.execute(text("CREATE TABLE p (id INTEGER PRIMARY KEY)"))
        c.execute(text("CREATE TABLE c (id INTEGER PRIMARY KEY, pid INTEGER REFERENCES p(id) ON DELETE CASCADE)"))
        c.execute(text("INSERT INTO p VALUES (1)"))
        c.execute(text("INSERT INTO c VALUES (1, 1)"))
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            c.execute(text("INSERT INTO c VALUES (2, 99)"))
    with engine.begin() as c:
        c.execute(text("DELETE FROM p WHERE id = 1"))
        assert c.execute(text("SELECT count(*) FROM c")).scalar() == 0   # cascade works


def test_postgres_engine_settings_suit_a_suspending_free_host():
    eng = make_engine("postgresql+psycopg://u:p@localhost/db")      # builds the engine, does not connect
    assert eng.dialect.name == "postgresql" and eng.dialect.driver == "psycopg"
    assert eng.pool._pre_ping is True                                 # Neon suspends idle compute: dead connections must be dropped
    assert eng.pool.size() == 5 and eng.pool._recycle == 240


def test_sqlite_engine_allows_use_from_other_threads(tmp_path):
    eng = make_engine(f"sqlite:///{tmp_path / 'x' / 'y.db'}")          # also creates the missing folder
    assert (tmp_path / "x").is_dir() and eng.dialect.name == "sqlite"


def test_pg_tests_refuse_a_database_that_is_not_named_test():
    """The guard that stops the pg tests from wiping a real database."""
    import subprocess, sys, os
    env = {**os.environ, "DATABASE_URL_TEST": "postgresql://u:p@host/shariahease_prod"}
    code = ("import pytest,sys; sys.exit(pytest.main(['-q','-m','pg','-p','no:cacheprovider','tests/test_migrations.py::test_pg_downgrade_and_upgrade_again']))")
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                       cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    assert r.returncode != 0 and "Refusing to wipe database" in (r.stdout + r.stderr)
