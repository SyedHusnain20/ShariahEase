"""
Path step 5b on a REAL PostgreSQL (opt in: python -m pytest -m pg, with DATABASE_URL_TEST set to a THROWAWAY database):
the Pro button's row is counted by scripts/gate_metrics.sql (distinct, non-admin people, before as_of), two taps that
arrive at the same moment still leave one row, and deleting the account removes the row.
"""
import importlib.util
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database.migrate import upgrade_to_head
from app.services import account_service, upgrade_interest

pytestmark = pytest.mark.pg

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("gate_report_pg_interest", SCRIPTS / "gate_report.py")
gate_report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate_report)

COHORT_START = datetime(2026, 12, 1, 0, 0, tzinfo=timezone.utc)
AS_OF = datetime(2027, 3, 14, 6, 30, tzinfo=timezone.utc)


@pytest.fixture
def engine(pg_test_url):
    eng = create_engine(pg_test_url, pool_size=8)
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
    upgrade_to_head(eng)
    yield eng
    with eng.begin() as c:
        c.execute(text("TRUNCATE users CASCADE"))
    eng.dispose()


def add_user(engine, uid, admin=False):
    with engine.begin() as c:
        c.execute(text("INSERT INTO users (id, name, email, hashed_password, timezone, is_admin, created_at) "
                       "VALUES (:i, 'U', :e, 'x', 'Asia/Karachi', :a, :t)"),
                  {"i": uid, "e": f"u{uid}@x.com", "a": admin, "t": COHORT_START})


def gate_interest(engine, as_of=AS_OF):
    return gate_report.run_sql(engine, gate_report.parameters("gate", COHORT_START, as_of))["upgrade_interest_users"]


def tap(engine, uid):
    with sessionmaker(bind=engine)() as db:
        return upgrade_interest.record_upgrade_interest(db, uid)


def set_time(engine, uid, when):
    with engine.begin() as c:
        c.execute(text("UPDATE audit_log SET created_at = :t WHERE user_id = :u AND action = 'upgrade_interest'"), {"t": when, "u": uid})


def test_the_gate_counts_one_person_once_however_often_they_tapped(engine):
    add_user(engine, 1)
    assert [tap(engine, 1), tap(engine, 1), tap(engine, 1)] == [True, False, False]
    set_time(engine, 1, AS_OF - timedelta(days=1))
    assert gate_interest(engine) == 1
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM audit_log WHERE action = 'upgrade_interest'")).scalar() == 1


def test_the_gate_leaves_out_admins_and_taps_at_or_after_as_of(engine):
    for uid, admin in ((1, False), (2, True), (3, False), (4, False)):
        add_user(engine, uid, admin)
        tap(engine, uid)
    set_time(engine, 1, AS_OF - timedelta(seconds=1))
    set_time(engine, 2, AS_OF - timedelta(days=2))          # an admin
    set_time(engine, 3, AS_OF)                              # exactly at as_of: not counted
    set_time(engine, 4, AS_OF - timedelta(days=5))
    assert gate_interest(engine) == 2                       # persons 1 and 4


def test_the_row_has_a_real_null_detail_and_no_entity(engine):
    add_user(engine, 1)
    tap(engine, 1)
    with engine.connect() as c:
        row = c.execute(text("SELECT actor, entity_type, entity_id, detail IS NULL FROM audit_log WHERE action = 'upgrade_interest'")).one()
    assert tuple(row) == ("user", None, None, True)


def test_two_taps_at_the_same_moment_leave_one_row(engine):
    add_user(engine, 1)
    results, barrier = [], threading.Barrier(6)

    def worker():
        barrier.wait()
        results.append(tap(engine, 1))

    threads = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == [False] * 5 + [True]
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM audit_log WHERE action = 'upgrade_interest'")).scalar() == 1


def test_deleting_the_account_removes_the_row(engine):
    add_user(engine, 1)
    add_user(engine, 2)
    tap(engine, 1)
    tap(engine, 2)
    with sessionmaker(bind=engine)() as db:
        assert account_service.delete_account(db, 1) is True
    with engine.connect() as c:
        assert [r[0] for r in c.execute(text("SELECT user_id FROM audit_log WHERE action = 'upgrade_interest'"))] == [2]
