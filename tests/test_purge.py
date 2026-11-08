"""The 30-day purge and the once-a-day maintenance runner. Offline (SQLite); the real-Postgres race is in test_account_and_purge_pg.py."""
import json
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.database import models
from app.routers import expenses as expenses_router
from app.services import expense_service as es
from tests.test_expense_api import H, add, api, body  # noqa: F401  (api is a fixture)


def make_expense(db, user_id, *, deleted_ago=None, now, note=None):
    e = models.Expense(user_id=user_id, client_id=uuid.uuid4(), amount_paisa=100, occurred_on=date(2026, 10, 1),
                       category_id=1, note=note)
    if deleted_ago is not None:
        e.deleted_at = now - deleted_ago
    db.add(e)
    db.commit()
    return e.id


def ids(db):
    return {r[0] for r in db.execute(text("SELECT id FROM expenses")).all()}


@pytest.fixture
def now(clock):
    return clock["now"]


# ── the cut-off ──────────────────────────────────────────────────────────────────

def test_purge_removes_only_rows_deleted_more_than_30_days_ago(db_session, make_user, now):
    u = make_user()
    old = make_expense(db_session, u, deleted_ago=timedelta(days=30, seconds=1), now=now)
    edge = make_expense(db_session, u, deleted_ago=timedelta(days=30), now=now)
    just_inside = make_expense(db_session, u, deleted_ago=timedelta(days=29, hours=23, minutes=59), now=now)
    recent = make_expense(db_session, u, deleted_ago=timedelta(days=1), now=now)
    live = make_expense(db_session, u, now=now)
    assert es.PURGE_AFTER_DAYS == 30
    assert es.purge_deleted_expenses(db_session) == 1
    assert ids(db_session) == {edge, just_inside, recent, live} and old not in ids(db_session)


def test_purge_never_touches_a_live_row_however_old(db_session, make_user, now):
    u = make_user()
    live = make_expense(db_session, u, now=now)
    db_session.execute(text("UPDATE expenses SET created_at = '2020-01-01 00:00:00', occurred_on = '2020-01-01' WHERE id = :i"), {"i": live})
    db_session.commit()
    assert es.purge_deleted_expenses(db_session) == 0 and ids(db_session) == {live}


def test_purge_covers_every_user(db_session, make_user, now):
    a, b = make_user(), make_user()
    x = make_expense(db_session, a, deleted_ago=timedelta(days=45), now=now)
    y = make_expense(db_session, b, deleted_ago=timedelta(days=45), now=now)
    keep = make_expense(db_session, b, now=now)
    assert es.purge_deleted_expenses(db_session) == 2
    assert ids(db_session) == {keep}


def test_purge_uses_the_clock(db_session, make_user, now, clock):
    u = make_user()
    e = make_expense(db_session, u, deleted_ago=timedelta(days=10), now=now)
    assert es.purge_deleted_expenses(db_session) == 0
    clock["now"] = now + timedelta(days=21)
    assert es.purge_deleted_expenses(db_session) == 1 and e not in ids(db_session)


def test_a_restored_row_is_not_purged(api, clock):
    c = api()
    keep = add(c, note="restore me")
    c.delete(f"/api/expenses/{keep['id']}", headers=H)
    clock["now"] += timedelta(days=10)
    assert c.post(f"/api/expenses/{keep['id']}/restore", headers=H).status_code == 200
    clock["now"] += timedelta(days=60)
    with api.Session() as db:
        assert es.purge_deleted_expenses(db) == 0
    assert c.get("/api/expenses/export").json()["count"] == 1


def test_restore_stops_working_after_the_purge(api, clock):
    c = api()
    e = add(c)
    c.delete(f"/api/expenses/{e['id']}", headers=H)
    clock["now"] += timedelta(days=31)
    with api.Session() as db:
        assert es.purge_deleted_expenses(db) == 1
    assert c.post(f"/api/expenses/{e['id']}/restore", headers=H).status_code == 404


def test_purge_writes_one_audit_row_with_a_count_and_nothing_else(db_session, make_user, now):
    u = make_user()
    for _ in range(3):
        make_expense(db_session, u, deleted_ago=timedelta(days=40), now=now, note="private words")
    es.purge_deleted_expenses(db_session)
    rows = db_session.execute(text("SELECT user_id, actor, action, entity_id, detail FROM audit_log WHERE action = 'expense.purge'")).all()
    assert len(rows) == 1
    user_id, actor, action, entity_id, detail = rows[0]
    assert user_id is None and actor == "system" and entity_id is None
    assert (json.loads(detail) if isinstance(detail, str) else detail) == {"count": 3}
    assert "private words" not in str(rows)


def test_a_purge_that_removes_nothing_writes_no_audit_row(db_session, make_user, now):
    make_expense(db_session, make_user(), now=now)
    assert es.purge_deleted_expenses(db_session) == 0
    assert db_session.execute(text("SELECT count(*) FROM audit_log WHERE action = 'expense.purge'")).scalar() == 0


@pytest.mark.parametrize("bad", [0, -1, True, "30", 1.5, None])
def test_purge_refuses_a_silly_retention(db_session, bad):
    with pytest.raises(es.InvalidInput):
        es.purge_deleted_expenses(db_session, retention_days=bad)


# ── the once-a-day runner ────────────────────────────────────────────────────────

def test_maintenance_runs_once_and_then_waits_a_day(db_session, make_user, now, clock):
    u = make_user()
    make_expense(db_session, u, deleted_ago=timedelta(days=40), now=now)
    assert es.run_maintenance(db_session) == {"ran": True, "purged": 1}
    make_expense(db_session, u, deleted_ago=timedelta(days=40), now=now)
    assert es.run_maintenance(db_session) == {"ran": False, "purged": 0}
    clock["now"] = now + timedelta(hours=23, minutes=59, seconds=59)
    assert es.run_maintenance(db_session) == {"ran": False, "purged": 0}
    assert len(ids(db_session)) == 1                                  # the second old row is still there
    clock["now"] = now + timedelta(hours=24)
    assert es.run_maintenance(db_session) == {"ran": True, "purged": 1}
    assert ids(db_session) == set()


def test_maintenance_records_its_run(db_session, now):
    es.run_maintenance(db_session)
    rows = db_session.execute(text("SELECT name, last_run_at FROM maintenance_runs")).all()
    assert len(rows) == 1 and rows[0][0] == es.MAINTENANCE_PURGE


def test_maintenance_never_raises_and_does_not_retry_until_the_interval(db_session, make_user, now, clock, monkeypatch, caplog):
    u = make_user()
    make_expense(db_session, u, deleted_ago=timedelta(days=40), now=now, note="secret note")

    def boom(*a, **k):
        raise RuntimeError("database exploded with secret note")
    real_purge = es.purge_deleted_expenses
    monkeypatch.setattr(es, "purge_deleted_expenses", boom)
    assert es.run_maintenance(db_session) == {"ran": False, "purged": 0}
    assert "secret note" not in caplog.text                          # the log line carries no data
    monkeypatch.setattr(es, "purge_deleted_expenses", real_purge)
    assert es.run_maintenance(db_session) == {"ran": False, "purged": 0}   # claimed already: next try is after the interval
    clock["now"] = now + timedelta(hours=24)
    assert es.run_maintenance(db_session) == {"ran": True, "purged": 1}


def test_maintenance_survives_a_broken_database(monkeypatch):
    class Broken:
        def execute(self, *a, **k):
            raise RuntimeError("down")
        def rollback(self):
            self.rolled_back = True
    db = Broken()
    assert es.run_maintenance(db) == {"ran": False, "purged": 0} and db.rolled_back


# ── started by normal traffic ────────────────────────────────────────────────────

def test_listing_expenses_starts_the_purge_at_most_once_a_day(api, clock):
    c = api()
    old = add(c, note="old")
    c.delete(f"/api/expenses/{old['id']}", headers=H)
    clock["now"] += timedelta(days=40)
    with api.Session() as db:
        assert ids(db) == {old["id"]}
    c.get("/api/expenses", params={"month": "2026-10"})
    with api.Session() as db:
        assert ids(db) == set()
        assert db.execute(text("SELECT count(*) FROM maintenance_runs")).scalar() == 1
    again = add(c, note="second")
    c.delete(f"/api/expenses/{again['id']}", headers=H)
    clock["now"] += timedelta(days=40)                                 # a day has long passed since the last run
    c.get("/api/expenses", params={"month": "2026-10"})
    with api.Session() as db:
        assert again["id"] not in ids(db)


def test_a_failing_purge_does_not_break_the_list_route(api, monkeypatch):
    c = api()
    add(c)
    monkeypatch.setattr(es, "purge_deleted_expenses", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    r = c.get("/api/expenses", params={"month": "2026-10"})
    assert r.status_code == 200 and len(r.json()["items"]) == 1
