from sqlalchemy import create_engine, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

# ── Which database? ───────────────────────────────────────────────────────────
# DATABASE_URL set  -> hosted Postgres (e.g. Neon). Set it as a Secret on the
#                      HF Space and in your local .env. Never commit it.
# DATABASE_URL unset -> SQLite file, exactly as before:
#                      DATABASE_PATH=/data/shariahease.db on HF Spaces,
#                      ./shariahease.db locally.
# NOTE: the free HF Space disk is ephemeral. Without DATABASE_URL, accounts and
# history can be lost whenever the Space restarts.


def build_database_url(env: dict) -> str:
    """Pure function (testable): turn the environment into a SQLAlchemy URL."""
    raw = (env.get("DATABASE_URL") or "").strip()
    if not raw:
        return "sqlite:///" + env.get("DATABASE_PATH", "./shariahease.db")
    # Neon / Heroku style URLs start with postgres:// or postgresql://.
    # We use the psycopg v3 driver.
    for prefix in ("postgres://", "postgresql://"):
        if raw.startswith(prefix):
            return "postgresql+psycopg://" + raw[len(prefix):]
    return raw


DATABASE_URL = build_database_url(os.environ)
IS_SQLITE    = DATABASE_URL.startswith("sqlite")
DB_PATH      = os.getenv("DATABASE_PATH", "./shariahease.db")

def make_engine(url: str):
    """Build the engine for a URL (separate function so the settings can be tested without connecting)."""
    if url.startswith("sqlite"):
        # Ensure the directory exists (e.g. /data/ on HF Spaces)
        sqlite_file = url[len("sqlite:///"):]
        if sqlite_file and sqlite_file != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(sqlite_file)), exist_ok=True)
        return create_engine(url, connect_args={"check_same_thread": False})  # needed for SQLite only
    # Neon Free suspends compute after ~5 minutes idle: pre-ping drops dead connections,
    # a short recycle avoids stale ones, a small pool keeps us inside the free connection limits.
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5, pool_recycle=240)


engine = make_engine(DATABASE_URL)


def enable_sqlite_foreign_keys(eng) -> None:
    """SQLite ignores foreign keys unless asked; turn them on so it behaves like Postgres."""
    @event.listens_for(eng, "connect")
    def _fk_on(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


if IS_SQLITE:
    enable_sqlite_foreign_keys(engine)

# Each request gets its own session, closed after
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# All ORM models inherit from this
Base = declarative_base()


# Dependency — inject into FastAPI routes with Depends()
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
