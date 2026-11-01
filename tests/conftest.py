import os
import sys

import httpx
import pytest

# Make `import app...` work when pytest is run from the project root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# llm_client builds a Groq client at import time; the tests never call it.
os.environ.setdefault("GROQ_API_KEY", "test-key-not-used")


@pytest.fixture(autouse=True)
def no_real_price_requests(monkeypatch):
    """
    No test may reach a real price API. Every price request fails instantly as
    if offline, and the quote cache starts empty. Tests that exercise the
    providers install their own transport (see tests/test_stock_price.py).
    """
    from app.services import stock_price

    def offline(request):
        raise httpx.ConnectError("offline (tests)", request=request)

    monkeypatch.setattr(
        stock_price, "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(offline)),
    )
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    def reset():
        stock_price._cache.clear()
        stock_price._psx_token = None
        stock_price._psx_token_failed_at = None
        stock_price._psx_board = None

    reset()
    yield
    reset()


# ── Phase 0: database and HTTP fixtures (offline: in-memory SQLite) ────────────

@pytest.fixture
def db_engine():
    """In-memory SQLite built by the real Alembic migrations, foreign keys ON (like Postgres)."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from app.database.db import enable_sqlite_foreign_keys
    from app.database.migrate import upgrade_to_head

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    enable_sqlite_foreign_keys(engine)
    upgrade_to_head(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine):
    from sqlalchemy.orm import sessionmaker
    session = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)()
    yield session
    session.close()


@pytest.fixture
def client(db_engine):
    """A FastAPI app with the auth, chat and webhook routers on the test database."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker
    from app.database.db import get_db
    from app.routers import auth, chatbot, webhook

    app = FastAPI()
    for r in (auth.router, chatbot.router, webhook.router):
        app.include_router(r)
    Session = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)

    def _get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _get_db
    with TestClient(app) as c:
        c.session_factory = Session
        yield c


def signup(client, email="a@example.com", name="Amina", password="secret123"):
    r = client.post("/auth/signup", json={"name": name, "email": email, "password": password})
    assert r.status_code == 201, r.text
    return r.json()


# ── Phase 1: expense fixtures ─────────────────────────────────────────────────

import itertools
import uuid
from datetime import date, datetime, timezone

_user_counter = itertools.count(1)


@pytest.fixture
def make_user(db_session):
    """Creates a user row and returns its id. timezone defaults to the app default."""
    from app.database.models import User

    def _make(timezone="Asia/Karachi", email=None):
        n = next(_user_counter)
        u = User(name=f"User {n}", email=email or f"user{n}@example.com", hashed_password="x", timezone=timezone)
        db_session.add(u)
        db_session.commit()
        return u.id
    return _make


@pytest.fixture
def clock(monkeypatch):
    """Freezes the expense service's clock. Change clock['now'] inside a test to move time."""
    from app.services import expense_service
    state = {"now": datetime(2026, 10, 31, 7, 0, tzinfo=timezone.utc)}      # 12:00 on 2026-10-31 in Karachi
    monkeypatch.setattr(expense_service, "_utcnow", lambda: state["now"])
    return state


_UNSET = object()


def exp(amount=10000, on=date(2026, 10, 30), sub=_UNSET, **kw):
    """One valid expense input (a dict). Override any field with keyword arguments."""
    if sub is _UNSET:                                         # default subcategory, unless a category was chosen
        sub = None if "category_slug" in kw else "food.groceries"
    data = dict(client_id=uuid.uuid4(), amount_paisa=amount, occurred_on=on, subcategory_slug=sub)
    data.update(kw)
    return data


@pytest.fixture
def pg_test_url():
    """
    URL of a THROWAWAY Postgres database for the `pg` tests (DATABASE_URL_TEST).
    These tests wipe the public schema, so the database name must contain "test":
    this stops a copy-paste of the production URL from erasing real data.
    """
    import os
    from sqlalchemy.engine import make_url
    raw = os.getenv("DATABASE_URL_TEST")
    if not raw:
        pytest.skip("DATABASE_URL_TEST not set")
    if raw.startswith(("postgres://", "postgresql://")):
        raw = "postgresql+psycopg://" + raw.split("://", 1)[1]
    name = (make_url(raw).database or "").lower()
    if "test" not in name:
        pytest.fail(f"Refusing to wipe database {name!r}: DATABASE_URL_TEST must name a throwaway database containing 'test'.")
    return raw
