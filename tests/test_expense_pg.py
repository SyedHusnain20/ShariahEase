"""
Expense service on a real PostgreSQL (opt in: python -m pytest -m pg, with DATABASE_URL_TEST set to a THROWAWAY
database). Covers what SQLite cannot show: database-level constraints, sums done by Postgres, real concurrency,
and that the partial and descending indexes exist as designed.
"""
import random
import threading
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.database.migrate import upgrade_to_head
from app.database.models import Expense, User
from app.services import expense_service as es
from tests.conftest import exp

pytestmark = pytest.mark.pg


@pytest.fixture
def pg(pg_test_url, monkeypatch):
    engine = create_engine(pg_test_url, pool_size=30, max_overflow=10)
    with engine.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
    upgrade_to_head(engine)
    monkeypatch.setattr(es, "_utcnow", lambda: datetime(2027, 3, 1, 7, 0, tzinfo=timezone.utc))
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    yield factory
    engine.dispose()


def _user(factory, n=1):
    with factory() as s:
        ids = []
        for i in range(n):
            u = User(name="U", email=f"u{uuid.uuid4().hex[:8]}@x.com", hashed_password="x")
            s.add(u)
            s.flush()
            ids.append(u.id)
        s.commit()
    return ids if n > 1 else ids[0]


def test_seed_and_constraints_on_postgres(pg):
    uid = _user(pg)
    with pg() as s:
        assert s.execute(text("SELECT count(*) FROM categories")).scalar() == 13
        assert s.execute(text("SELECT count(*) FROM subcategories")).scalar() == 40
        # identity counters work after the seed (no explicit ids were inserted)
        s.execute(text("INSERT INTO categories (slug,name_en,name_ur) VALUES ('zz','Z','ز')"))
        s.commit()
    for bad in (dict(amount_paisa=0), dict(amount_paisa=10 ** 12 + 1), dict(occurred_on="1999-12-31"),
                dict(note="a" * 501), dict(currency="USD"), dict(source="x"), dict(payment_method="x"), dict(shariah_flag="x")):
        with pg() as s:
            row = dict(user_id=uid, client_id=str(uuid.uuid4()), amount_paisa=100, occurred_on="2026-10-30", category_id=1)
            row.update(bad)
            with pytest.raises(IntegrityError):
                s.execute(text(f"INSERT INTO expenses ({', '.join(row)}) VALUES ({', '.join(':' + k for k in row)})"), row)
                s.commit()
    with pg() as s:                                                              # 500 Urdu letters pass: the limit is in characters
        s.execute(text("INSERT INTO expenses (user_id,client_id,amount_paisa,occurred_on,category_id,note) "
                       "VALUES (:u, gen_random_uuid(), 1, '2026-10-30', 1, :n)"), {"u": uid, "n": "ا" * 500})
        s.commit()


def test_the_composite_foreign_key_on_postgres(pg):
    uid = _user(pg)
    with pg() as s:
        groceries = s.execute(text("SELECT id, category_id FROM subcategories WHERE slug='food.groceries'")).one()
        transport = s.execute(text("SELECT id FROM categories WHERE slug='transport'")).scalar()
        with pytest.raises(IntegrityError):
            s.execute(text("INSERT INTO expenses (user_id,client_id,amount_paisa,occurred_on,category_id,subcategory_id) "
                           "VALUES (:u, gen_random_uuid(), 1, '2026-10-30', :c, :s)"), {"u": uid, "c": transport, "s": groceries.id})
            s.commit()


def test_indexes_are_partial_and_descending_as_designed(pg):
    with pg() as s:
        defs = {r[0]: r[1] for r in s.execute(text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename IN ('expenses','audit_log')"))}
    assert "WHERE (deleted_at IS NULL)" in defs["ix_exp_user_date"] and "occurred_on DESC, id DESC" in defs["ix_exp_user_date"]
    assert "WHERE (deleted_at IS NULL)" in defs["ix_exp_user_cat_date"] and "occurred_on DESC" in defs["ix_exp_user_cat_date"]
    assert "subcategory_id IS NOT NULL" in defs["ix_exp_user_sub_date"] and "deleted_at IS NULL" in defs["ix_exp_user_sub_date"]
    assert "WHERE (deleted_at IS NOT NULL)" in defs["ix_exp_purge"]
    assert "created_at DESC" in defs["ix_audit_user_time"]


def test_the_month_list_uses_the_user_date_index(pg):
    uid = _user(pg)
    rng = random.Random(7)
    with pg() as s:
        s.execute(text("INSERT INTO expenses (user_id,client_id,amount_paisa,occurred_on,category_id) "
                       "SELECT :u, gen_random_uuid(), 1 + (g % 5000), DATE '2026-01-01' + (g % 300), 1 + (g % 13) "
                       "FROM generate_series(1, 20000) g"), {"u": uid})
        s.commit()
        s.execute(text("ANALYZE expenses"))
        plan = "\n".join(r[0] for r in s.execute(text(
            "EXPLAIN SELECT * FROM expenses WHERE user_id = :u AND deleted_at IS NULL "
            "AND occurred_on >= DATE '2026-10-01' AND occurred_on < DATE '2026-11-01' "
            "ORDER BY occurred_on DESC, id DESC LIMIT 50"), {"u": uid}))
    assert "ix_exp_user_date" in plan, plan


def test_sums_by_postgres_equal_an_independent_python_sum(pg):
    uid, other = _user(pg, 2)
    rng = random.Random(11)
    with pg() as s:
        cats = es.list_categories(s)
        subs = [(c.slug, x.slug) for c in cats for x in c.subcategories]
        added = []
        for _ in range(200):
            cat, sub = rng.choice(subs)
            amount = rng.choice([1, 12345, 10 ** 9, 10 ** 12, rng.randint(1, 10 ** 6)])
            on = date(2026, 8, 1) + timedelta(days=rng.randrange(150))
            es.add_expenses(s, uid, [exp(amount, on, sub=sub)])
            added.append((on, amount, cat, sub))
        for _ in range(50):
            es.add_expenses(s, other, [exp(rng.randint(1, 10 ** 6), date(2026, 10, rng.randint(1, 28)))])
        d_from, d_to = date(2026, 9, 1), date(2026, 11, 1)
        rows = [r for r in added if d_from <= r[0] < d_to]
        got = es.summarize(s, uid, date_from=d_from, date_to=d_to)
        assert (got.total_paisa, got.count) == (sum(r[1] for r in rows), len(rows))
        by_cat = es.summarize(s, uid, date_from=d_from, date_to=d_to, group_by="category")
        want = {}
        for _on, amt, cat, _sub in rows:
            want[cat] = want.get(cat, 0) + amt
        assert {g.key: g.total_paisa for g in by_cat.groups} == want
        by_month = es.summarize(s, uid, date_from=d_from, date_to=d_to, group_by="month")
        want_m = {}
        for on, amt, *_ in rows:
            want_m[f"{on.year}-{on.month:02d}"] = want_m.get(f"{on.year}-{on.month:02d}", 0) + amt
        assert {g.key: g.total_paisa for g in by_month.groups} == want_m
        by_sub = es.summarize(s, uid, date_from=d_from, date_to=d_to, group_by="subcategory")
        assert by_sub.total_paisa == sum(g.total_paisa for g in by_sub.groups)
        by_day = es.summarize(s, uid, date_from=d_from, date_to=d_to, group_by="day")
        assert by_day.total_paisa == sum(g.total_paisa for g in by_day.groups) == got.total_paisa


def test_twenty_parallel_retries_with_one_client_id_make_one_row(pg):
    uid = _user(pg)
    cid = uuid.uuid4()
    results, errors = [], []

    def worker():
        try:
            with pg() as s:
                results.append(es.add_expenses(s, uid, [exp(500, client_id=cid)])[0].created)
        except Exception as exc:                       # pragma: no cover - reported by the assert below
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(20)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == [] and results.count(True) == 1 and results.count(False) == 19
    with pg() as s:
        assert s.scalar(select(func.count()).select_from(Expense)) == 1
        assert s.execute(text("SELECT count(*) FROM audit_log WHERE action='expense.create'")).scalar() == 1


def test_parallel_edits_with_the_same_version_have_exactly_one_winner(pg):
    uid = _user(pg)
    with pg() as s:
        eid = es.add_expenses(s, uid, [exp(100)])[0].expense.id
    outcomes = []

    def worker(i):
        with pg() as s:
            try:
                es.update_expense(s, uid, eid, 1, {"amount_paisa": 1000 + i})
                outcomes.append("won")
            except es.VersionConflict:
                outcomes.append("conflict")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert outcomes.count("won") == 1 and outcomes.count("conflict") == 19
    with pg() as s:
        assert s.execute(text("SELECT version FROM expenses")).scalar() == 2


def test_parallel_deletes_change_the_row_once(pg):
    uid = _user(pg)
    with pg() as s:
        eid = es.add_expenses(s, uid, [exp(100)])[0].expense.id
    changed = []

    def worker():
        with pg() as s:
            changed.append(len(es.delete_expenses(s, uid, [eid]).changed))

    threads = [threading.Thread(target=worker) for _ in range(20)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(changed) == 1
    with pg() as s:
        assert s.execute(text("SELECT version FROM expenses")).scalar() == 2
        assert s.execute(text("SELECT count(*) FROM audit_log WHERE action='expense.delete'")).scalar() == 1


def test_authorization_on_postgres(pg):
    a, b = _user(pg, 2)
    with pg() as s:
        ea = es.add_expenses(s, a, [exp(1000)])[0].expense
        assert es.get_expenses(s, b, expense_ids=[ea.id]).items == []
        with pytest.raises(es.NotFound):
            es.update_expense(s, b, ea.id, 1, {"amount_paisa": 1})
        assert es.delete_expenses(s, b, [ea.id]).changed == []
        assert es.summarize(s, b, date_from=date(2026, 1, 1), date_to=date(2027, 1, 1)).total_paisa == 0


def test_deleting_a_user_removes_their_rows_on_postgres(pg):
    a, b = _user(pg, 2)
    with pg() as s:
        es.add_expenses(s, a, [exp(1)])
        es.add_expenses(s, b, [exp(2)])
        s.execute(text("DELETE FROM users WHERE id = :u"), {"u": a})
        s.commit()
        assert s.execute(text("SELECT count(*) FROM expenses")).scalar() == 1
        assert s.execute(text("SELECT count(*) FROM audit_log")).scalar() == 1
