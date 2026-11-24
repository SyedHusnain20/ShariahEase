"""
Update/delete confirmation cards on a REAL PostgreSQL (Path step 5a; opt in: python -m pytest -m pg tests/test_edit_card_pg.py with
DATABASE_URL_TEST set to a THROWAWAY database; the tests wipe its public schema).

  * 30 parallel confirms of one card: exactly one changes the entry, once (version +1, one audit row),
  * a confirm racing with a manual edit of the same entry: exactly one of the two wins, no change is lost or applied twice,
  * 30 parallel picks never leave a half-written card, and a delete card racing an undo ends in one consistent state.
"""
import threading
import uuid
from datetime import date

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database.migrate import upgrade_to_head
from app.database.models import User
from app.services import expense_service as es, pending_actions as pa

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


def _entry(factory, uid):
    with factory() as s:
        out = es.add_expenses(s, uid, [{"client_id": uuid.uuid4(), "amount_paisa": 300000, "occurred_on": date.today(),
                                        "subcategory_slug": "transport.fuel", "note": "petrol"}])
        return out[0].expense.id


def _card(factory, uid, expense_id, action="update", chosen=0, new_amount=350000):
    target = pa.EditTarget(expense_id=expense_id, version=1, amount_paisa=300000, category_slug="transport.fuel",
                           occurred_on=date.today(), note="petrol")
    changes = pa.EditChanges(new_amount_paisa=new_amount) if action == "update" else None
    with factory() as s:
        row, _ = pa.create_card(s, uid, client_msg_id=uuid.uuid4().hex, payload=pa.EditPayload(
            action=action, candidates=[target], chosen=chosen, changes=changes))
        return row.id


def _parallel(n, work):
    out, barrier = [], threading.Barrier(n)

    def run(i):
        barrier.wait()
        out.append(work(i))
    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    return out


def test_thirty_parallel_confirms_change_the_entry_exactly_once(pg):
    for _ in range(3):
        uid = _user(pg)
        eid, cid = _entry(pg, uid), None
        cid = _card(pg, uid, eid)

        def work(_):
            with pg() as s:
                try:
                    return ("ok", len(pa.confirm(s, uid, cid)))
                except pa.CardError as err:
                    return (err.code, 0)
        outcomes = _parallel(30, work)
        assert [o for o in outcomes if o[0] == "ok"] == [("ok", 1)] and all(o[0] == "already_used" for o in outcomes if o[0] != "ok")
        with pg() as s:
            row = s.execute(text("SELECT amount_paisa, version FROM expenses WHERE id = :i"), {"i": eid}).one()
            assert tuple(row) == (350000, 2)
            assert s.execute(text("SELECT count(*) FROM audit_log WHERE action = 'expense.update' AND user_id = :u"), {"u": uid}).scalar() == 1


def test_a_confirm_racing_a_manual_edit_has_exactly_one_winner_and_loses_nothing(pg):
    for round_ in range(10):
        uid = _user(pg)
        eid = _entry(pg, uid)
        cid = _card(pg, uid, eid)

        def work(i):
            with pg() as s:
                if i == 0:
                    try:
                        pa.confirm(s, uid, cid)
                        return "card"
                    except pa.CardError as err:
                        return "card:" + err.code
                try:
                    es.update_expense(s, uid, eid, 1, {"amount_paisa": 310000})
                    return "manual"
                except es.VersionConflict:
                    return "manual:conflict"
        outcomes = _parallel(2, work)
        with pg() as s:
            amount, version = tuple(s.execute(text("SELECT amount_paisa, version FROM expenses WHERE id = :i"), {"i": eid}).one())
        assert version == 2, (round_, outcomes)                                   # exactly one change landed, never two, never none
        winner = "card" if "card" in outcomes else "manual"
        assert amount == {"card": 350000, "manual": 310000}[winner], (round_, outcomes)
        assert sorted(outcomes) in (["card", "manual:conflict"], ["card:stale", "manual"]), outcomes


def test_thirty_parallel_picks_leave_one_complete_choice(pg):
    uid = _user(pg)
    ids = [_entry(pg, uid) for _ in range(3)]
    targets = [pa.EditTarget(expense_id=i, version=1, amount_paisa=300000, category_slug="transport.fuel", occurred_on=date.today())
               for i in ids]
    with pg() as s:
        row, _ = pa.create_card(s, uid, client_msg_id="m", payload=pa.EditPayload(action="delete", candidates=targets))
        cid = row.id

    def work(i):
        with pg() as s:
            try:
                pa.pick(s, uid, cid, i % 3)
                return i % 3
            except pa.CardError as err:
                return err.code
    outcomes = _parallel(30, work)
    assert all(o in (0, 1, 2, "conflict") for o in outcomes) and any(isinstance(o, int) for o in outcomes)
    with pg() as s:
        payload = s.get(pa.PendingAction, cid).payload
        assert payload["chosen"] in (0, 1, 2) and len(payload["candidates"]) == 3 and payload["action"] == "delete"


def test_a_confirmed_delete_and_a_parallel_undo_end_in_one_consistent_state(pg):
    uid = _user(pg)
    eid = _entry(pg, uid)
    cid = _card(pg, uid, eid, action="delete")
    with pg() as s:
        pa.confirm(s, uid, cid)

    def work(_):
        with pg() as s:
            try:
                return pa.undo(s, uid, cid)
            except pa.CardError as err:
                return err.code
    outcomes = _parallel(10, work)
    assert outcomes.count(1) == 1 and all(o == "already_used" or o == "not_confirmed" or o == 1 for o in outcomes)
    with pg() as s:
        assert s.execute(text("SELECT deleted_at IS NULL FROM expenses WHERE id = :i"), {"i": eid}).scalar() is True
