"""Account deletion (DELETE /auth/me): password re-entry, nothing left behind, nobody else touched. Offline."""
import uuid
from datetime import date

import pytest
from sqlalchemy import inspect, text

from app.database import models
from app.routers import auth as auth_router
from app.routers import expenses as expenses_router
from app.services import account_service
from tests.test_expense_api import H, add, api, body  # noqa: F401  (api is a fixture)

PASSWORD = "secret123"          # what tests.conftest.signup uses


@pytest.fixture(autouse=True)
def fresh_limiters():
    auth_router.delete_limiter.reset()
    expenses_router.write_limiter.reset()
    yield
    auth_router.delete_limiter.reset()


def delete_me(c, password=PASSWORD, headers=H, **extra):
    return c.request("DELETE", "/auth/me", json={"password": password, **extra}, headers=headers)


# ── every table that holds a user's data: discovered from the real schema ────────────────────────

def user_tables(engine):
    """(table, column) for every column named user_id or referencing users.id, in the migrated database."""
    insp = inspect(engine)
    found = {}
    for table in insp.get_table_names():
        if table == "users":
            continue
        cols = {c["name"] for c in insp.get_columns(table)}
        if "user_id" in cols:
            found.setdefault(table, set()).add("user_id")
        for fk in insp.get_foreign_keys(table):
            if fk["referred_table"] == "users":
                found.setdefault(table, set()).update(fk["constrained_columns"])
    return found


def _zakat(db, uid):
    db.add(models.ZakatRecord(user_id=uid, user_name="U", total_assets=1, total_deductions=0, zakatable_wealth=1,
                              nisab_threshold=1, zakat_due=1))


def _screener(db, uid):
    db.add(models.ScreenerHistory(user_id=uid, query="AAPL", verdict="halal"))


def _chat(db, uid):
    db.add(models.ChatMessage(user_id=uid, session_id="s1", role="user", content="hello"))


def _audit(db, uid):
    db.add(models.AuditLog(user_id=uid, actor="user", action="expense.create", entity_type="expense", entity_id="1"))


def _expense(db, uid):
    db.add(models.Expense(user_id=uid, client_id=uuid.uuid4(), amount_paisa=100, occurred_on=date(2026, 10, 30), category_id=1))


def _refresh(db, uid):
    from app.services import refresh_tokens
    refresh_tokens.issue_refresh_token(db, uid, None)


# One seeder per table that holds user data. A new table with a user_id fails test_every_user_table_has_a_seeder
# until someone adds it here AND makes sure deleting the account removes it.
SEEDERS = {
    "zakat_records": _zakat, "screener_history": _screener, "chat_messages": _chat,
    "audit_log": _audit, "expenses": _expense, "refresh_tokens": _refresh,
}


def count_rows(db, table, user_id):
    return db.execute(text(f"SELECT count(*) FROM {table} WHERE user_id = :u"), {"u": user_id}).scalar()


def seed_everything(db, user_id):
    for table, seeder in SEEDERS.items():
        seeder(db, user_id)
    db.commit()
    for table in SEEDERS:
        assert count_rows(db, table, user_id) >= 1, f"seeding {table} failed"


def user_id_of(db, email):
    return db.execute(text("SELECT id FROM users WHERE email = :e"), {"e": email}).scalar()


# ── the schema itself ────────────────────────────────────────────────────────────────────────────

def test_every_user_table_has_a_seeder(db_engine):
    assert set(user_tables(db_engine)) == set(SEEDERS), (
        "a table now references users: add a seeder for it in SEEDERS and make sure it cascades")


def _orm_cascaded_tables():
    """Tables whose rows the ORM deletes together with a User (relationship cascade 'delete')."""
    return {rel.mapper.local_table.name for rel in models.User.__mapper__.relationships if rel.cascade.delete}


def test_every_table_that_references_users_is_cleaned_up_in_the_real_schema(db_engine):
    """
    Database-level ON DELETE CASCADE is required everywhere, except on SQLite for the three legacy tables: migration 0002
    cannot alter their constraint there and the ORM relationship cascade covers them instead (see account_service).
    The strict check on a real PostgreSQL is in test_account_deletion_pg.py.
    """
    insp = inspect(db_engine)
    orm = _orm_cascaded_tables()
    for table, columns in user_tables(db_engine).items():
        fks = [fk for fk in insp.get_foreign_keys(table) if fk["referred_table"] == "users"]
        assert fks, f"{table} has a user column but no foreign key to users"
        for fk in fks:
            db_cascade = (fk["options"].get("ondelete") or "").upper() == "CASCADE"
            assert db_cascade or table in orm, (
                f"{table}.{fk['constrained_columns']} neither cascades in the database nor through a User relationship")


def test_every_declared_model_foreign_key_to_users_cascades():
    for table in models.Base.metadata.tables.values():
        for fk in table.foreign_keys:
            if fk.column.table.name == "users":
                assert (fk.ondelete or "").upper() == "CASCADE", f"{table.name}.{fk.parent.name}"


def test_the_seeded_tables_cover_the_declared_models():
    declared = {t.name for t in models.Base.metadata.tables.values()
                if any(fk.column.table.name == "users" for fk in t.foreign_keys)}
    assert declared == set(SEEDERS)


# ── the route: who may call it and with what ─────────────────────────────────────────────────────

def test_needs_login(api):
    r = api(login=False).request("DELETE", "/auth/me", json={"password": PASSWORD}, headers=H)
    assert r.status_code == 401


def test_needs_login_even_without_a_body(api):
    assert api(login=False).request("DELETE", "/auth/me", headers=H).status_code == 401


def test_missing_anti_forgery_header_is_403_and_deletes_nothing(api):
    c = api("m@example.com")
    for headers in ({}, {"X-Requested-With": "XMLHttpRequest"}, {"X-Requested-With": ""}):
        assert delete_me(c, headers=headers).status_code == 403
    with api.Session() as db:
        assert user_id_of(db, "m@example.com") is not None


def test_header_value_matches_the_expense_routes():
    assert auth_router.REQUIRED_HEADER == ("X-Requested-With", expenses_router.REQUIRED_HEADER_VALUE)


@pytest.mark.parametrize("password", ["wrong-password", "SECRET123", " secret123", "secret123 ", "secret12"])
def test_wrong_password_is_403_and_deletes_nothing(api, password):
    c = api("w@example.com")
    r = delete_me(c, password=password)
    assert r.status_code == 403 and r.json()["detail"] == {"code": "wrong_password"}
    with api.Session() as db:
        assert user_id_of(db, "w@example.com") is not None
    assert c.get("/auth/me").status_code == 200                      # still logged in


def test_bad_bodies_are_422_and_delete_nothing(api):
    c = api("b@example.com")
    for payload in ({}, {"password": ""}, {"password": "x" * 129}, {"password": 123}, {"password": PASSWORD, "user_id": 1}, {"pw": PASSWORD}):
        assert c.request("DELETE", "/auth/me", json=payload, headers=H).status_code == 422, payload
    with api.Session() as db:
        assert user_id_of(db, "b@example.com") is not None


def test_a_body_cannot_name_another_user(api):
    a, b = api("a@example.com"), api("victim@example.com")
    r = delete_me(a, user_id=2, email="victim@example.com")
    assert r.status_code == 422
    with api.Session() as db:
        assert user_id_of(db, "victim@example.com") is not None and user_id_of(db, "a@example.com") is not None


# ── limiter ──────────────────────────────────────────────────────────────────────────────────────

def test_password_guessing_is_limited_and_the_limit_also_blocks_the_right_password(api):
    c = api("l@example.com")
    for _ in range(auth_router.DELETE_ATTEMPTS):
        assert delete_me(c, password="nope").status_code == 403
    r = delete_me(c, password=PASSWORD)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    assert r.json()["detail"]["code"] == "rate_limited"
    with api.Session() as db:
        assert user_id_of(db, "l@example.com") is not None


def test_the_limit_is_per_user(api):
    a, b = api("a1@example.com"), api("b1@example.com")
    for _ in range(auth_router.DELETE_ATTEMPTS):
        delete_me(a, password="nope")
    assert delete_me(a).status_code == 429
    assert delete_me(b).status_code == 200


def test_the_right_password_works_on_the_last_allowed_attempt(api):
    c = api("last@example.com")
    for _ in range(auth_router.DELETE_ATTEMPTS - 1):
        assert delete_me(c, password="nope").status_code == 403
    assert delete_me(c).status_code == 200


# ── the deletion itself ──────────────────────────────────────────────────────────────────────────

def test_deleting_leaves_no_row_in_any_user_table_and_touches_nobody_else(api, db_engine):
    a, b = api("gone@example.com"), api("stays@example.com")
    with api.Session() as db:
        a_id, b_id = user_id_of(db, "gone@example.com"), user_id_of(db, "stays@example.com")
        seed_everything(db, a_id)
        seed_everything(db, b_id)
        before = {t: count_rows(db, t, b_id) for t in user_tables(db_engine)}
    add(a, note="mine"), add(b, note="theirs")
    r = delete_me(a)
    assert r.status_code == 200 and r.json() == {"detail": "Account deleted"}
    with api.Session() as db:
        assert db.execute(text("SELECT count(*) FROM users WHERE id = :u"), {"u": a_id}).scalar() == 0
        for table in user_tables(db_engine):
            assert count_rows(db, table, a_id) == 0, f"{table} still has rows of the deleted user"
        for table, n in before.items():                               # the other user lost nothing (b added one more expense + audit)
            assert count_rows(db, table, b_id) >= n, table
        assert db.execute(text("SELECT count(*) FROM users WHERE id = :u"), {"u": b_id}).scalar() == 1


def test_soft_deleted_expenses_are_removed_too(api, db_engine):
    c = api("sd@example.com")
    e = add(c)
    c.delete(f"/api/expenses/{e['id']}", headers=H)
    with api.Session() as db:
        uid = user_id_of(db, "sd@example.com")
    assert delete_me(c).status_code == 200
    with api.Session() as db:
        assert count_rows(db, "expenses", uid) == 0


def test_cookies_are_cleared_and_the_old_tokens_stop_working(api):
    c = api("tok@example.com")
    access = c.cookies.get("access_token")
    refresh = c.cookies.get("refresh_token")
    r = delete_me(c)
    assert r.status_code == 200
    cleared = " ".join(r.headers.get_list("set-cookie")).lower()
    assert "access_token=" in cleared and "refresh_token=" in cleared and ("max-age=0" in cleared or "expires=" in cleared)
    other = api(login=False)
    assert other.get("/auth/me", headers={"Authorization": f"Bearer {access}"}).status_code == 401
    other.cookies.set("refresh_token", refresh, path="/auth")
    assert other.post("/auth/refresh").status_code == 401


def test_the_account_cannot_log_in_again_and_a_second_delete_is_401(api):
    c = api("again@example.com")
    assert delete_me(c).status_code == 200
    fresh = api(login=False)
    assert fresh.post("/auth/login", json={"email": "again@example.com", "password": PASSWORD}).status_code == 401
    assert delete_me(c).status_code == 401


def test_the_same_email_can_sign_up_again_with_a_clean_slate(api):
    c = api("reborn@example.com")
    add(c, note="old")
    assert delete_me(c).status_code == 200
    n = api(login=False)
    assert n.post("/auth/signup", json={"name": "New", "email": "reborn@example.com", "password": PASSWORD}).status_code == 201
    assert n.get("/api/expenses/export").json()["count"] == 0


def test_the_service_refuses_a_bad_user_id(db_session):
    for bad in (0, -1, "1", None, True, 1.0):
        with pytest.raises(TypeError):
            account_service.delete_account(db_session, bad)
    assert account_service.delete_account(db_session, 999) is False
