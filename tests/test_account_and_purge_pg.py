"""
Account deletion and the 30-day purge on a REAL PostgreSQL (opt in: python -m pytest -m pg, with DATABASE_URL_TEST set to a
THROWAWAY database). SQLite cannot show the strict database-level cascade, the partial purge index, or two workers racing.
"""
import threading
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.database.migrate import upgrade_to_head
from app.database.models import Expense, MaintenanceRun, User
from app.services import account_service
from app.services import expense_service as es
from tests.test_account_deletion import SEEDERS, count_rows, seed_everything, user_tables

pytestmark = pytest.mark.pg

NOW = datetime(2027, 3, 1, 7, 0, tzinfo=timezone.utc)


@pytest.fixture
def pg(pg_test_url, monkeypatch):
    engine = create_engine(pg_test_url, pool_size=30, max_overflow=10)
    with engine.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
    upgrade_to_head(engine)
    clock = {"now": NOW}
    monkeypatch.setattr(es, "_utcnow", lambda: clock["now"])
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    factory.engine, factory.clock = engine, clock
    yield factory
    engine.dispose()


def _user(factory):
    with factory() as s:
        u = User(name="U", email=f"u{uuid.uuid4().hex[:8]}@x.com", hashed_password="x")
        s.add(u)
        s.flush()
        uid = u.id
        s.commit()
    return uid


def _expense(s, uid, *, deleted_days_ago=None, note=None):
    e = Expense(user_id=uid, client_id=uuid.uuid4(), amount_paisa=100, occurred_on=date(2027, 2, 1), category_id=1, note=note)
    if deleted_days_ago is not None:
        e.deleted_at = NOW - timedelta(days=deleted_days_ago)
    s.add(e)
    s.flush()
    return e.id


# ── account deletion: strict cascade ─────────────────────────────────────────────

def test_every_table_that_references_users_cascades_in_the_database(pg):
    insp = inspect(pg.engine)
    tables = user_tables(pg.engine)
    assert set(tables) == set(SEEDERS)
    for table in tables:
        fks = [fk for fk in insp.get_foreign_keys(table) if fk["referred_table"] == "users"]
        assert fks, table
        for fk in fks:
            assert (fk["options"].get("ondelete") or "").upper() == "CASCADE", f"{table}.{fk['constrained_columns']}"


def test_deleting_an_account_removes_every_row_and_nothing_of_anyone_else(pg):
    a, b = _user(pg), _user(pg)
    with pg() as s:
        seed_everything(s, a)
        seed_everything(s, b)
        before = {t: count_rows(s, t, b) for t in SEEDERS}
    with pg() as s:
        assert account_service.delete_account(s, a) is True
    with pg() as s:
        for table in SEEDERS:
            assert count_rows(s, table, a) == 0, table
            assert count_rows(s, table, b) == before[table], table
        assert s.execute(text("SELECT count(*) FROM users WHERE id = :u"), {"u": a}).scalar() == 0
        assert s.execute(text("SELECT count(*) FROM users WHERE id = :u"), {"u": b}).scalar() == 1


def test_a_database_cascade_alone_removes_the_rows_even_without_the_orm(pg):
    """Belt and braces: a plain DELETE of the user row (no ORM) also leaves nothing behind on PostgreSQL."""
    a = _user(pg)
    with pg() as s:
        seed_everything(s, a)
    with pg() as s:
        s.execute(text("DELETE FROM users WHERE id = :u"), {"u": a})
        s.commit()
    with pg() as s:
        assert all(count_rows(s, t, a) == 0 for t in SEEDERS)


# ── purge on a real database ─────────────────────────────────────────────────────

def test_purge_cut_off_and_scope_on_postgres(pg):
    u1, u2 = _user(pg), _user(pg)
    with pg() as s:
        old1 = _expense(s, u1, deleted_days_ago=31)
        old2 = _expense(s, u2, deleted_days_ago=90)
        edge = _expense(s, u1, deleted_days_ago=30)                   # exactly 30 days: still undoable
        recent = _expense(s, u1, deleted_days_ago=5)
        live = _expense(s, u1)
        s.commit()
    with pg() as s:
        assert es.purge_deleted_expenses(s) == 2
    with pg() as s:
        left = {r[0] for r in s.execute(text("SELECT id FROM expenses")).all()}
        assert left == {edge, recent, live} and not ({old1, old2} & left)
        audit = s.execute(text("SELECT user_id, actor, action, detail FROM audit_log WHERE action = 'expense.purge'")).all()
        assert len(audit) == 1 and audit[0][0] is None and audit[0][1] == "system" and audit[0][3] == {"count": 2}


def test_workers_starting_maintenance_at_once_purge_once_every_round(pg):
    """16 workers start together, three days in a row: exactly one of them runs each time (an atomic claim)."""
    u = _user(pg)
    for round_no in range(3):
        pg.clock["now"] = NOW + timedelta(days=round_no)
        with pg() as s:
            for _ in range(10):
                e = _expense(s, u, deleted_days_ago=40 + round_no)
                s.execute(text("UPDATE expenses SET deleted_at = :d WHERE id = :i"),
                          {"d": pg.clock["now"] - timedelta(days=40), "i": e})
            s.commit()
        results, barrier = [], threading.Barrier(16)

        def worker():
            with pg() as s:
                barrier.wait()
                results.append(es.run_maintenance(s))

        threads = [threading.Thread(target=worker) for _ in range(16)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert sum(1 for r in results if r["ran"]) == 1, f"round {round_no}: {results}"
        assert sum(r["purged"] for r in results) == 10
        with pg() as s:
            assert s.execute(text("SELECT count(*) FROM expenses")).scalar() == 0
            assert s.execute(text("SELECT count(*) FROM audit_log WHERE action = 'expense.purge'")).scalar() == round_no + 1
            assert s.execute(text("SELECT count(*) FROM maintenance_runs")).scalar() == 1


def test_maintenance_runs_again_only_after_the_interval(pg):
    u = _user(pg)
    with pg() as s:
        _expense(s, u, deleted_days_ago=40)
        s.commit()
    with pg() as s:
        assert es.run_maintenance(s) == {"ran": True, "purged": 1}
    with pg() as s:
        _expense(s, u, deleted_days_ago=40)
        s.commit()
    pg.clock["now"] = NOW + timedelta(hours=23, minutes=59)
    with pg() as s:
        assert es.run_maintenance(s) == {"ran": False, "purged": 0}
    pg.clock["now"] = NOW + timedelta(hours=24)
    with pg() as s:
        assert es.run_maintenance(s) == {"ran": True, "purged": 1}
