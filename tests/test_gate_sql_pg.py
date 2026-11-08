"""
gate_metrics.sql on a REAL PostgreSQL, against the independent Python implementation, on random data that hugs every
boundary (opt in: python -m pytest -m pg, with DATABASE_URL_TEST set to a THROWAWAY database).
"""
import importlib.util
import random
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database.migrate import upgrade_to_head
from tests.gate_reference import make_dataset, reference_counts

pytestmark = pytest.mark.pg

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("gate_report_pg", SCRIPTS / "gate_report.py")
gate_report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate_report)

COHORT_START = datetime(2026, 12, 1, 0, 0, tzinfo=timezone.utc)
AS_OF = datetime(2027, 3, 14, 6, 30, tzinfo=timezone.utc)          # a few days before US daylight-saving time starts
COUNT_KEYS = ["starters", "starters_judgeable", "activated", "retention_base", "retained", "active_users_14d", "ai_users_14d",
              "ai_entries_eligible", "ai_entries_corrected", "upgrade_interest_users", "signup_to_first_entry_median_minutes"]


@pytest.fixture
def engine(pg_test_url):
    eng = create_engine(pg_test_url, pool_size=5)
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
    upgrade_to_head(eng)
    yield eng
    with eng.begin() as c:
        c.execute(text("TRUNCATE users CASCADE"))
    eng.dispose()


def load(engine, users, expense_specs, audit_specs):
    """Insert a dataset; returns the same rows with the real database ids, ready for the reference implementation."""
    with engine.begin() as c:
        c.execute(text("TRUNCATE users CASCADE"))
        for u in users:
            c.execute(text("INSERT INTO users (id, name, email, hashed_password, timezone, is_admin, created_at) "
                           "VALUES (:id, 'U', :email, 'x', :tz, :adm, :created)"),
                      {"id": u["id"], "email": f"u{u['id']}@x.com", "tz": u["tz"], "adm": u["is_admin"], "created": u["created_at"]})
        ids = {}
        for e in expense_specs:
            ids[e["key"]] = c.execute(text(
                "INSERT INTO expenses (user_id, client_id, amount_paisa, occurred_on, category_id, source, created_at, updated_at) "
                "VALUES (:u, :c, 100, :d, 1, :s, :t, :t) RETURNING id"),
                {"u": e["user_id"], "c": uuid.uuid4(), "d": date(2026, 12, 1), "s": e["source"], "t": e["created_at"]}).scalar()
        audit = []
        for a in audit_specs:
            entity_id = None if a["entity_key"] is None else str(ids[a["entity_key"]])
            c.execute(text("INSERT INTO audit_log (user_id, actor, action, entity_type, entity_id, created_at) "
                           "VALUES (:u, 'user', :a, :t, :e, :c)"),
                      {"u": a["user_id"], "a": a["action"], "t": a["entity_type"], "e": entity_id, "c": a["created_at"]})
            audit.append({**a, "entity_id": entity_id})
    return [{**e, "id": ids[e["key"]]} for e in expense_specs], audit


def sql_counts(engine, mode, cohort_start=COHORT_START, as_of=AS_OF):
    return gate_report.run_sql(engine, gate_report.parameters(mode, cohort_start, as_of))


def reference(users, expenses, audit, mode, cohort_start=COHORT_START, as_of=AS_OF):
    params = gate_report.parameters(mode, cohort_start, as_of)
    return reference_counts(users, expenses, audit, params["cohort_start"], params["as_of"], params["cohort_end"])


# ── the SQL equals the independent implementation ────────────────────────────────────────────────

@pytest.mark.parametrize("seed", range(18))
def test_sql_equals_the_independent_implementation_on_random_data(engine, seed):
    rng = random.Random(seed)
    users, expense_specs, audit_specs = make_dataset(rng, COHORT_START, AS_OF)
    expenses, audit = load(engine, users, expense_specs, audit_specs)
    for mode in ("gate", "c1"):
        got = sql_counts(engine, mode)
        want = reference(users, expenses, audit, mode)
        assert {k: got[k] for k in COUNT_KEYS} == want, f"seed {seed} mode {mode}"


def test_the_random_data_is_rich_enough_to_matter(engine):
    """Guard against a generator that silently stops exercising the rules: across the seeds every metric is non-trivial."""
    seen = {k: 0 for k in COUNT_KEYS}
    for seed in range(18):
        users, es, au = make_dataset(random.Random(seed), COHORT_START, AS_OF)
        expenses, audit = load(engine, users, es, au)
        for k, v in reference(users, expenses, audit, "gate").items():
            seen[k] = max(seen[k], v or 0)
        for k, v in reference(users, expenses, audit, "c1").items():
            seen[k] = max(seen[k], v or 0)
    assert all(v > 0 for v in seen.values()), seen
    assert seen["retained"] >= 2 and seen["ai_entries_corrected"] >= 2 and seen["activated"] >= 4


# ── session settings ─────────────────────────────────────────────────────────────────────────────

def test_the_session_time_zone_of_the_database_does_not_change_the_answer(engine, pg_test_url):
    """run_sql sets the session to UTC, so a database whose default zone has daylight saving gives the same counts."""
    dbname = engine.url.database
    users, es, au = make_dataset(random.Random(7), COHORT_START, AS_OF)
    expenses, audit = load(engine, users, es, au)
    want = reference(users, expenses, audit, "gate")
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
        c.execute(text(f'ALTER DATABASE "{dbname}" SET timezone TO \'America/New_York\''))
    try:
        other = create_engine(pg_test_url)
        assert other.connect().execute(text("SHOW timezone")).scalar() == "America/New_York"
        got = gate_report.run_sql(other, gate_report.parameters("gate", COHORT_START, AS_OF))
        assert {k: got[k] for k in COUNT_KEYS} == want
        other.dispose()
    finally:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
            c.execute(text(f'ALTER DATABASE "{dbname}" RESET timezone'))


def test_the_report_cannot_write(engine, tmp_path, monkeypatch):
    bad = tmp_path / "bad.sql"
    bad.write_text("INSERT INTO maintenance_runs (name, last_run_at) VALUES ('x', now()) RETURNING 1 AS starters;", encoding="utf-8")
    monkeypatch.setattr(gate_report, "SQL_FILE", bad)
    with pytest.raises(Exception, match="read-only"):
        sql_counts(engine, "c1")
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM maintenance_runs")).scalar() == 0


def test_the_real_sql_file_only_selects():
    lines = (SCRIPTS / "gate_metrics.sql").read_text(encoding="utf-8").splitlines()
    sql = "\n".join(line for line in lines if not line.lstrip().startswith("--")).upper()      # comments may say "create"
    for word in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ", "TRUNCATE ", "CREATE "):
        assert word not in sql, word


# ── hand-built cases (readable examples of each rule) ────────────────────────────────────────────

def _one_user(engine, tz="Asia/Karachi", signup=COHORT_START - timedelta(days=1)):
    users = [{"id": 1, "created_at": signup, "tz": tz, "is_admin": False}]
    return users


def test_activation_needs_two_local_days_in_the_users_own_time_zone(engine):
    first = datetime(2026, 12, 3, 12, 0, tzinfo=timezone.utc)            # an old cohort member (as_of is much later)
    # 18:59 and 19:00 UTC are on different days in Karachi (UTC+5) but on the same day in UTC.
    specs = [{"key": i, "user_id": 1, "created_at": t, "source": "manual"} for i, t in enumerate(
        [first.replace(hour=18, minute=59), first.replace(hour=19, minute=0), first.replace(hour=19, minute=1)], 1)]
    for tz, expected in (("Asia/Karachi", 1), ("UTC", 0)):
        users = _one_user(engine, tz)
        expenses, audit = load(engine, users, specs, [])
        got = sql_counts(engine, "gate")
        assert got["starters"] == 1 and got["activated"] == expected, tz
        assert got == {**got, **{k: v for k, v in reference(users, expenses, audit, "gate").items()}}


def test_soft_deleted_expenses_still_count_as_created(engine):
    users = _one_user(engine)
    first = datetime(2026, 12, 3, 5, 0, tzinfo=timezone.utc)
    specs = [{"key": i, "user_id": 1, "created_at": first + timedelta(days=i), "source": "manual"} for i in range(3)]
    expenses, _ = load(engine, users, specs, [])
    with engine.begin() as c:
        c.execute(text("UPDATE expenses SET deleted_at = now()"))
    assert sql_counts(engine, "gate")["activated"] == 1


def test_admins_are_left_out(engine):
    users = [{"id": 1, "created_at": COHORT_START - timedelta(days=1), "tz": "UTC", "is_admin": True}]
    specs = [{"key": i, "user_id": 1, "created_at": COHORT_START + timedelta(days=i), "source": "ai_chat"} for i in range(1, 4)]
    load(engine, users, specs, [])
    got = sql_counts(engine, "c1")
    assert got["starters"] == 0 and got["active_users_14d"] == 0 and got["ai_entries_eligible"] == 0


def test_daylight_saving_in_the_database_zone_cannot_move_a_window(engine, pg_test_url):
    """
    First expense 2027-03-10 12:00Z, third one 2027-03-17 11:30Z. US daylight saving starts on 2027-03-14, so in a New York
    session "+ 7 days" lands at 11:00Z and the third expense falls OUTSIDE the window; in UTC it is inside (12:00Z).
    The same SQL therefore gives different answers by session zone, and run_sql must give the UTC one.
    """
    as_of = datetime(2027, 4, 20, tzinfo=timezone.utc)
    users = [{"id": 1, "created_at": COHORT_START, "tz": "UTC", "is_admin": False}]
    times = [datetime(2027, 3, 10, 12, 0, tzinfo=timezone.utc), datetime(2027, 3, 11, 12, 0, tzinfo=timezone.utc),
             datetime(2027, 3, 17, 11, 30, tzinfo=timezone.utc)]
    load(engine, users, [{"key": i, "user_id": 1, "created_at": t, "source": "manual"} for i, t in enumerate(times, 1)], [])
    params = gate_report.parameters("gate", COHORT_START, as_of)
    dbname = engine.url.database
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
        c.execute(text(f'ALTER DATABASE "{dbname}" SET timezone TO \'America/New_York\''))
    other = create_engine(pg_test_url)
    try:
        with other.connect() as c:                                   # a plain New York session, no SET LOCAL
            assert c.execute(text("SHOW timezone")).scalar() == "America/New_York"
            naive = c.execute(text((SCRIPTS / "gate_metrics.sql").read_text(encoding="utf-8")), params).mappings().one()
        assert naive["activated"] == 0, "the test data no longer shows a daylight-saving difference"
        assert gate_report.run_sql(other, params)["activated"] == 1
    finally:
        other.dispose()
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
            c.execute(text(f'ALTER DATABASE "{dbname}" RESET timezone'))
