"""
The atomic allowance on a real PostgreSQL (opt in: python -m pytest -m pg tests/test_ai_quota_pg.py, with DATABASE_URL_TEST set
to a THROWAWAY database). 60 messages at once against a limit of 30: exactly 30 pass, in every run.
"""
import threading
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database.migrate import upgrade_to_head
from app.database.models import User
from app.services import ai_limits as lim

pytestmark = pytest.mark.pg


@pytest.fixture
def pg(pg_test_url):
    engine = create_engine(pg_test_url, pool_size=70, max_overflow=10)
    with engine.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
    upgrade_to_head(engine)
    yield sessionmaker(bind=engine, autoflush=False, autocommit=False)
    engine.dispose()


def _user(factory):
    with factory() as s:
        u = User(name="U", email=f"u{uuid.uuid4().hex[:8]}@x.com", hashed_password="x")
        s.add(u)
        s.commit()
        return u.id


def _race(factory, uid, workers, limit):
    results, barrier = [], threading.Barrier(workers)

    def work():
        with factory() as s:
            barrier.wait()
            results.append(lim.try_consume(s, uid, "2026-11", limit=limit))
    threads = [threading.Thread(target=work) for _ in range(workers)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    return results


def test_sixty_at_once_against_a_limit_of_thirty_lets_exactly_thirty_through(pg):
    uid = _user(pg)
    for _ in range(3):                                                  # the first insert and the later updates both race
        uid = _user(pg)
        results = _race(pg, uid, 60, 30)
        assert sorted(r for r in results if r is not None) == list(range(1, 31)) and results.count(None) == 30
        with pg() as s:
            assert lim.used_this_period(s, uid, "2026-11") == 30


def test_parallel_releases_never_go_below_zero_and_never_exceed_what_was_used(pg):
    uid = _user(pg)
    with pg() as s:
        for _ in range(5):
            lim.try_consume(s, uid, "2026-11", limit=30)
    barrier = threading.Barrier(20)

    def work():
        with pg() as s:
            barrier.wait()
            lim.release(s, uid, "2026-11")
    threads = [threading.Thread(target=work) for _ in range(20)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    with pg() as s:
        assert lim.used_this_period(s, uid, "2026-11") == 0


def test_a_card_can_be_confirmed_by_only_one_of_many_parallel_requests(pg):
    from datetime import date
    from app.services import pending_actions as pa
    uid = _user(pg)
    payload = pa.CardPayload(items=[pa.CardItem(amount_paisa=100, category_slug="food", occurred_on=date.today(), alternatives=["food"])])
    with pg() as s:
        row, _ = pa.create_card(s, uid, client_msg_id="m", payload=payload)
        cid = row.id
    outcomes, barrier = [], threading.Barrier(30)

    def work():
        with pg() as s:
            barrier.wait()
            try:
                outcomes.append(("ok", len(pa.confirm(s, uid, cid))))
            except pa.CardError as err:
                outcomes.append((err.code, 0))
    threads = [threading.Thread(target=work) for _ in range(30)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert [o for o in outcomes if o[0] == "ok"] == [("ok", 1)] and all(o[0] == "already_used" for o in outcomes if o[0] != "ok")
    with pg() as s:
        assert s.execute(text("SELECT count(*) FROM expenses WHERE user_id = :u"), {"u": uid}).scalar() == 1


def test_the_new_tables_cascade_on_postgres(pg):
    from datetime import datetime, timedelta, timezone
    uid = _user(pg)
    with pg() as s:
        s.execute(text("INSERT INTO usage_counters (user_id, period, used) VALUES (:u, '2026-11', 1)"), {"u": uid})
        s.execute(text("INSERT INTO pending_actions (id, user_id, kind, status, client_msg_id, payload, expires_at) "
                       "VALUES (:i, :u, 'create', 'pending', 'm', '{}', :e)"), {"i": uuid.uuid4(), "u": uid, "e": datetime.now(timezone.utc) + timedelta(minutes=5)})
        s.commit()
        s.execute(text("DELETE FROM users WHERE id = :u"), {"u": uid})
        s.commit()
        assert s.execute(text("SELECT (SELECT count(*) FROM usage_counters) + (SELECT count(*) FROM pending_actions)")).scalar() == 0
