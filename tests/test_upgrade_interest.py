"""
Path step 5b: the "Notify me about Pro" button. One tap writes ONE audit_log row per person (action `upgrade_interest`,
actor `user`, no entity, no detail); a second tap writes nothing and gives the same thanks. Login, the write header and
the write limiter apply; nobody can record interest for somebody else; account deletion removes the row.
Offline (SQLite). The gate query itself is checked on PostgreSQL in tests/test_upgrade_interest_pg.py.
"""
import pytest

from app.routers import expenses
from app.services import account_service, upgrade_interest
from tests.test_expense_ai_flow import H, web  # noqa: F401  (web is a fixture)

URL = "/api/expense-ai/upgrade-interest"


def interest_rows(web):
    return [tuple(r) for r in web.rows(
        "SELECT user_id, actor, action, entity_type, entity_id, detail FROM audit_log WHERE action = 'upgrade_interest' ORDER BY id")]


def user_ids(web):
    return [r[0] for r in web.rows("SELECT id FROM users ORDER BY id")]


# ── what one tap writes ─────────────────────────────────────────────────────────

def test_one_tap_writes_one_row_with_nothing_but_the_person_and_the_action(web):
    me = web.make()
    r = me.post(URL, headers=H)
    assert r.status_code == 200 and r.json() == {"status": "recorded"}
    assert interest_rows(web) == [(user_ids(web)[0], "user", "upgrade_interest", None, None, None)]


def test_a_second_and_third_tap_write_nothing_and_still_say_thanks(web):
    me = web.make()
    first = me.post(URL, headers=H)
    again = [me.post(URL, headers=H) for _ in range(3)]
    assert first.json()["status"] == "recorded"
    assert all(r.status_code == 200 and r.json() == {"status": "already_recorded"} for r in again)
    assert len(interest_rows(web)) == 1


def test_each_person_gets_their_own_single_row(web):
    a, b = web.make(), web.make()
    a.post(URL, headers=H)
    b.post(URL, headers=H)
    b.post(URL, headers=H)
    assert sorted(r[0] for r in interest_rows(web)) == user_ids(web)


def test_the_request_has_no_way_to_name_another_person_or_add_details(web):
    me, other = web.make(), web.make()
    other_id = user_ids(web)[1]
    r = me.post(URL, headers=H, json={"user_id": other_id, "detail": {"email": "x@example.com"}, "entity_id": "7"})
    assert r.status_code == 200
    assert interest_rows(web) == [(user_ids(web)[0], "user", "upgrade_interest", None, None, None)]    # mine, and nothing extra


def test_the_service_says_whether_this_call_wrote_the_row(web):
    web.make()
    uid = user_ids(web)[0]
    with web.Session() as db:
        assert upgrade_interest.has_recorded(db, uid) is False
        assert upgrade_interest.record_upgrade_interest(db, uid) is True
        assert upgrade_interest.has_recorded(db, uid) is True
    with web.Session() as db:                                        # a new session, as a new request would have
        assert upgrade_interest.record_upgrade_interest(db, uid) is False
    assert len(interest_rows(web)) == 1


def test_only_that_action_counts_as_interest(web):
    web.make()
    uid = user_ids(web)[0]
    with web.Session() as db:
        from app.database.models import AuditLog
        db.add(AuditLog(user_id=uid, actor="user", action="expense.create", entity_type="expense", entity_id="1"))
        db.commit()
        assert upgrade_interest.has_recorded(db, uid) is False
        assert upgrade_interest.record_upgrade_interest(db, uid) is True


# ── who may tap ─────────────────────────────────────────────────────────────────

def test_a_guest_cannot_tap(web):
    guest = web.make(login=False)
    r = guest.post(URL, headers=H)
    assert r.status_code == 401
    assert interest_rows(web) == []


def test_the_write_header_is_required(web):
    me = web.make()
    r = me.post(URL)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "missing_header"
    assert me.post(URL, headers={"X-Requested-With": "something-else"}).status_code == 403
    assert interest_rows(web) == []


def test_the_button_shares_the_write_limit(web):
    me = web.make()
    for _ in range(expenses.WRITES_PER_MINUTE):
        assert me.post(URL, headers=H).status_code == 200
    r = me.post(URL, headers=H)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    assert len(interest_rows(web)) == 1


def test_a_get_does_not_record_anything(web):
    me = web.make()
    assert me.get(URL).status_code == 405
    assert interest_rows(web) == []


# ── what the page can learn ────────────────────────────────────────────────────

def test_status_tells_the_page_whether_this_person_already_tapped(web):
    a, b = web.make(), web.make()
    assert a.get("/api/expense-ai/status").json()["upgrade_interest"] is False
    a.post(URL, headers=H)
    assert a.get("/api/expense-ai/status").json()["upgrade_interest"] is True
    assert b.get("/api/expense-ai/status").json()["upgrade_interest"] is False        # not somebody else's tap


# ── deletion and privacy ───────────────────────────────────────────────────────

def test_deleting_the_account_removes_the_row(web):
    a, b = web.make(), web.make()
    a.post(URL, headers=H)
    b.post(URL, headers=H)
    a_id, b_id = user_ids(web)
    with web.Session() as db:
        assert account_service.delete_account(db, a_id) is True
    assert [r[0] for r in interest_rows(web)] == [b_id]


def test_the_privacy_page_describes_the_button_and_the_date_moved(web):
    import re
    from app.routers import pages
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles
    from fastapi.testclient import TestClient
    from tests.test_privacy_and_settings import STATIC
    app = FastAPI()
    app.include_router(pages.router)
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
    html = TestClient(app).get("/privacy").text
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    assert pages.PRIVACY_UPDATED == "26 November 2026" and "Last updated: 26 November 2026" in text
    assert "Pro interest" in text and "Notify me about Pro" in text
    assert "no email address" in text and "no payment details" in text and "deleted with your account" in text
    assert "has no AI feature yet" not in text                      # the old sentence contradicted the AI cards below it
