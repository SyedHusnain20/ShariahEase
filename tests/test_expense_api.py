"""REST API for expenses: login, ownership (404), stale edits (409), write limit (429), money, privacy. Offline."""
import logging
import uuid

import pytest

H = {"X-Requested-With": "ShariahEase"}
NOTE = "SECRET-NOTE-zq81 Ali ko udhaar"


@pytest.fixture
def api(db_engine, clock, monkeypatch):
    """Returns make(): a fresh TestClient (own cookie jar) on one shared app and database."""
    from passlib.context import CryptContext
    from app.services import auth_service
    # test speed only: the cheapest bcrypt cost (production keeps its normal cost)
    monkeypatch.setattr(auth_service, "pwd_context", CryptContext(schemes=["bcrypt"], bcrypt__rounds=4))
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker
    from app.database.db import get_db
    from app.routers import auth, expenses

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(expenses.router)
    Session = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)

    def _get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _get_db
    expenses.write_limiter.reset()
    clients = []

    def make(email=None, login=True):
        c = TestClient(app)
        clients.append(c)
        if login:
            from tests.conftest import signup
            signup(c, email=email or f"u{len(clients)}@example.com")
        return c

    make.Session = Session
    yield make
    for c in clients:
        c.close()
    expenses.write_limiter.reset()


def body(**kw):
    data = {"client_id": str(uuid.uuid4()), "amount": "250", "occurred_on": "2026-10-30",
            "subcategory_slug": "food.groceries"}
    data.update(kw)
    return data


def add(c, **kw):
    r = c.post("/api/expenses", json=body(**kw), headers=H)
    assert r.status_code in (200, 201), r.text
    return r.json()["expense"]


# ── login required on every route ────────────────────────────────────────────

ROUTES = [
    ("get", "/api/expenses/categories"), ("get", "/api/expenses?month=2026-10"),
    ("get", "/api/expenses/summary?month=2026-10"), ("post", "/api/expenses"),
    ("patch", "/api/expenses/1"), ("delete", "/api/expenses/1"), ("post", "/api/expenses/1/restore"),
]


@pytest.mark.parametrize("method,path", ROUTES)
def test_every_route_needs_login(api, method, path):
    c = api(login=False)
    kwargs = {"headers": H}
    if method in ("post", "patch"):
        kwargs["json"] = body() if path == "/api/expenses" else {"version": 1}
    r = getattr(c, method)(path, **kwargs)
    assert r.status_code == 401, (method, path, r.status_code)


def test_bearer_token_works_without_the_cookie(api):
    c = api()
    token = c.cookies.get("access_token")
    other = api(login=False)
    r = other.get("/api/expenses/categories", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and len(r.json()) == 13


def test_a_bad_token_is_401(api):
    c = api(login=False)
    assert c.get("/api/expenses/categories", headers={"Authorization": "Bearer nonsense"}).status_code == 401


# ── create ───────────────────────────────────────────────────────────────────

def test_create_returns_201_and_paisa(api):
    c = api()
    r = c.post("/api/expenses", json=body(amount="1,250.50", note="  atta  "), headers=H)
    assert r.status_code == 201
    e = r.json()["expense"]
    assert r.json()["status"] == "created" and e["amount_paisa"] == 125050 and e["note"] == "atta"
    assert e["category_slug"] == "food" and e["version"] == 1 and e["source"] == "manual"


def test_retry_with_same_client_id_is_200_and_writes_nothing_new(api):
    c = api()
    b = body()
    first = c.post("/api/expenses", json=b, headers=H)
    again = c.post("/api/expenses", json=b, headers=H)
    assert first.status_code == 201 and again.status_code == 200 and again.json()["status"] == "duplicate"
    assert again.json()["expense"]["id"] == first.json()["expense"]["id"]
    assert len(c.get("/api/expenses?month=2026-10").json()["items"]) == 1


def test_retry_of_a_deleted_expense_is_409_not_resurrected(api):
    c = api()
    b = body()
    eid = c.post("/api/expenses", json=b, headers=H).json()["expense"]["id"]
    assert c.delete(f"/api/expenses/{eid}", headers=H).status_code == 200
    r = c.post("/api/expenses", json=b, headers=H)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "already_deleted"
    assert c.get("/api/expenses?month=2026-10").json()["items"] == []


@pytest.mark.parametrize("amount", ["0", "0.00", "-5", "abc", "1.234", "1e3", "", "10000000001", 0, -3, 12.5, True, None, [], "1,0000"])
def test_bad_amounts_are_422_on_the_amount_field(api, amount):
    c = api()
    r = c.post("/api/expenses", json=body(amount=amount), headers=H)
    assert r.status_code == 422, (amount, r.text)
    assert c.get("/api/expenses?month=2026-10").json()["items"] == []


def test_money_error_names_the_field_and_a_code(api):
    c = api()
    r = c.post("/api/expenses", json=body(amount="1.234"), headers=H)
    assert r.json()["detail"] == {"code": "invalid_input", "errors": [{"field": "amount", "problem": "too_many_decimals"}]}


def test_urdu_digits_and_pkr_prefix(api):
    c = api()
    assert add(c, amount="۱۲۵۰")["amount_paisa"] == 125000
    assert add(c, amount="Rs. 99.5")["amount_paisa"] == 9950


@pytest.mark.parametrize("extra", [{"user_id": 2}, {"id": 5}, {"amount_paisa": 5}, {"version": 3}, {"deleted_at": "2026-01-01"}, {"source": "ai_chat"}])
def test_unknown_fields_are_refused(api, extra):
    c = api()
    r = c.post("/api/expenses", json={**body(), **extra}, headers=H)
    assert r.status_code == 422


def test_user_id_in_body_cannot_write_for_someone_else(api):
    a, b = api(), api()
    uid_b = b.cookies  # unused; ids are 1 and 2
    r = a.post("/api/expenses", json={**body(), "user_id": 2}, headers=H)
    assert r.status_code == 422
    assert b.get("/api/expenses?month=2026-10").json()["items"] == []


@pytest.mark.parametrize("patch", [
    {"subcategory_slug": "nope.nope"}, {"category_slug": "nope"}, {"payment_method": "crypto"},
    {"occurred_on": "2026-13-45"}, {"occurred_on": "2030-01-01"}, {"note": "x" * 501}, {"client_id": "not-a-uuid"},
])
def test_other_bad_input_is_422(api, patch):
    c = api()
    assert c.post("/api/expenses", json=body(**patch), headers=H).status_code == 422


def test_missing_json_body_is_422(api):
    assert api().post("/api/expenses", headers=H).status_code == 422


# ── anti-forgery header ──────────────────────────────────────────────────────

@pytest.mark.parametrize("hdr", [{}, {"X-Requested-With": "XMLHttpRequest"}, {"X-Requested-With": ""}])
def test_writes_without_the_header_are_403_and_do_nothing(api, hdr):
    c = api()
    eid = add(c)["id"]
    assert c.post("/api/expenses", json=body(), headers=hdr).status_code == 403
    assert c.patch(f"/api/expenses/{eid}", json={"version": 1, "note": "x"}, headers=hdr).status_code == 403
    assert c.delete(f"/api/expenses/{eid}", headers=hdr).status_code == 403
    assert c.post(f"/api/expenses/{eid}/restore", headers=hdr).status_code == 403
    items = c.get("/api/expenses?month=2026-10").json()["items"]
    assert len(items) == 1 and items[0]["version"] == 1


def test_reads_do_not_need_the_header(api):
    assert api().get("/api/expenses?month=2026-10").status_code == 200


# ── reading ──────────────────────────────────────────────────────────────────

def test_month_window_is_half_open(api):
    c = api()
    for d in ("2026-09-30", "2026-10-01", "2026-10-31"):
        add(c, occurred_on=d)
    r = c.get("/api/expenses?month=2026-10").json()
    assert sorted(i["occurred_on"] for i in r["items"]) == ["2026-10-01", "2026-10-31"]
    assert c.get("/api/expenses?month=2026-09").json()["items"][0]["occurred_on"] == "2026-09-30"


def test_december_rolls_into_next_year(api, clock):
    from datetime import datetime, timezone
    clock["now"] = datetime(2027, 1, 5, 7, 0, tzinfo=timezone.utc)
    c = api()
    add(c, occurred_on="2026-12-31")
    add(c, occurred_on="2027-01-01")
    items = c.get("/api/expenses?month=2026-12").json()["items"]
    assert [i["occurred_on"] for i in items] == ["2026-12-31"]


@pytest.mark.parametrize("q", ["month=2026-13", "month=2026-1", "month=abc", "month=2026-10-01", "month=1999-12",
                               "month=2026-10&date_from=2026-10-01", "date_from=2026-10-05&date_to=2026-10-01",
                               "month=2026-10&limit=0", "month=2026-10&limit=201", "month=2026-10&before_id=3",
                               "month=2026-10&category=" + "x" * 5])
def test_bad_queries_are_422(api, q):
    c = api()
    r = c.get(f"/api/expenses?{q}")
    assert r.status_code == 422, (q, r.text)


def test_paging_walks_every_row_once(api):
    c = api()
    ids = [add(c, occurred_on=f"2026-10-{d:02d}", amount=str(d))["id"] for d in (1, 5, 5, 9, 12, 12, 12, 20)]
    seen, url, pages = [], "/api/expenses?month=2026-10&limit=3", 0
    while True:
        pages += 1
        assert pages < 10
        r = c.get(url).json()
        seen += [i["id"] for i in r["items"]]
        if not r["next_before"]:
            break
        url = f"/api/expenses?month=2026-10&limit=3&before_date={r['next_before']['date']}&before_id={r['next_before']['id']}"
    assert sorted(seen) == sorted(ids) and len(seen) == len(set(seen))


def test_category_filter(api):
    c = api()
    add(c, subcategory_slug="food.groceries")
    add(c, subcategory_slug="transport.fuel")
    r = c.get("/api/expenses?month=2026-10&category=transport").json()["items"]
    assert [i["category_slug"] for i in r] == ["transport"]


def test_summary_totals_are_integer_paisa_and_exact(api):
    c = api()
    add(c, amount="0.10"); add(c, amount="0.20"); add(c, amount="19.99", subcategory_slug="transport.fuel")
    s = c.get("/api/expenses/summary?month=2026-10").json()
    assert s["total_paisa"] == 10 + 20 + 1999 and s["count"] == 3
    assert {g["key"]: g["total_paisa"] for g in s["groups"]} == {"food": 30, "transport": 1999}
    assert all(isinstance(g["total_paisa"], int) for g in s["groups"])
    assert c.get("/api/expenses/summary?month=2026-10&group_by=day").json()["groups"]
    assert c.get("/api/expenses/summary?month=2026-10&group_by=bogus").status_code == 422
    assert c.get("/api/expenses/summary").status_code == 422


def test_summary_and_list_leave_out_deleted_rows(api):
    c = api()
    e = add(c, amount="100")
    add(c, amount="50")
    c.delete(f"/api/expenses/{e['id']}", headers=H)
    assert c.get("/api/expenses/summary?month=2026-10").json()["total_paisa"] == 5000
    assert len(c.get("/api/expenses?month=2026-10").json()["items"]) == 1


# ── update ───────────────────────────────────────────────────────────────────

def test_update_bumps_the_version_and_changes_only_what_was_sent(api):
    c = api()
    e = add(c, amount="100", note="atta")
    r = c.patch(f"/api/expenses/{e['id']}", json={"version": 1, "amount": "175.25"}, headers=H)
    assert r.status_code == 200
    out = r.json()
    assert out["amount_paisa"] == 17525 and out["version"] == 2 and out["note"] == "atta"
    assert out["category_slug"] == "food" and out["occurred_on"] == "2026-10-30"


def test_update_can_clear_the_note_with_null(api):
    c = api()
    e = add(c, note="atta")
    out = c.patch(f"/api/expenses/{e['id']}", json={"version": 1, "note": None}, headers=H).json()
    assert out["note"] is None


def test_stale_edit_is_409_with_the_current_version_and_changes_nothing(api):
    c = api()
    e = add(c, amount="100")
    assert c.patch(f"/api/expenses/{e['id']}", json={"version": 1, "amount": "200"}, headers=H).status_code == 200
    r = c.patch(f"/api/expenses/{e['id']}", json={"version": 1, "amount": "999"}, headers=H)
    assert r.status_code == 409 and r.json()["detail"] == {"code": "version_conflict", "current_version": 2}
    assert c.get("/api/expenses?month=2026-10").json()["items"][0]["amount_paisa"] == 20000


@pytest.mark.parametrize("payload", [
    {}, {"version": 1}, {"version": "1", "note": "x"}, {"version": True, "note": "x"}, {"version": 0, "note": "x"},
    {"version": 1.0, "note": "x"}, {"version": 1, "user_id": 9}, {"version": 1, "amount_paisa": 5},
    {"version": 1, "amount": "5", "user_id": 9}, {"version": 1, "note": "x", "id": 77}, {"version": 1, "amount": "5", "deleted_at": "2026-01-01"},
    {"version": 1, "amount": None}, {"version": 1, "amount": "0"}, {"version": 1, "amount": 12.5},
    {"version": 1, "occurred_on": None}, {"version": 1, "payment_method": None}, {"version": 1, "deleted_at": "x"},
])
def test_bad_updates_are_422_and_change_nothing(api, payload):
    c = api()
    e = add(c, amount="100")
    r = c.patch(f"/api/expenses/{e['id']}", json=payload, headers=H)
    assert r.status_code == 422, (payload, r.status_code, r.text)
    row = c.get("/api/expenses?month=2026-10").json()["items"][0]
    assert row["version"] == 1 and row["amount_paisa"] == 10000


# ── delete and undo ──────────────────────────────────────────────────────────

def test_delete_then_restore_round_trip(api):
    c = api()
    e = add(c, amount="100", note="atta")
    r = c.delete(f"/api/expenses/{e['id']}", headers=H)
    assert r.status_code == 200 and r.json()["changed"] == [e["id"]]
    assert c.get("/api/expenses?month=2026-10").json()["items"] == []
    back = c.post(f"/api/expenses/{e['id']}/restore", headers=H)
    assert back.status_code == 200 and back.json()["expense"]["id"] == e["id"]
    assert back.json()["expense"]["note"] == "atta"
    assert len(c.get("/api/expenses?month=2026-10").json()["items"]) == 1


def test_deleting_twice_or_a_missing_id_is_404(api):
    c = api()
    e = add(c)
    assert c.delete(f"/api/expenses/{e['id']}", headers=H).status_code == 200
    assert c.delete(f"/api/expenses/{e['id']}", headers=H).status_code == 404
    assert c.delete("/api/expenses/99999", headers=H).status_code == 404
    assert c.post("/api/expenses/99999/restore", headers=H).status_code == 404
    assert c.post(f"/api/expenses/{add(c)['id']}/restore", headers=H).status_code == 404     # not deleted: nothing to undo


# ── ownership: another user's row is 404, never 403 or 409 ───────────────────

def test_another_users_expense_is_invisible_and_untouchable(api):
    a, b = api(), api()
    e = add(a, amount="100", note="private")
    eid = e["id"]
    assert b.get("/api/expenses?month=2026-10").json()["items"] == []
    assert b.get("/api/expenses/summary?month=2026-10").json()["total_paisa"] == 0
    assert b.patch(f"/api/expenses/{eid}", json={"version": 1, "amount": "1"}, headers=H).status_code == 404
    assert b.patch(f"/api/expenses/{eid}", json={"version": 77, "amount": "1"}, headers=H).status_code == 404   # never 409
    assert b.delete(f"/api/expenses/{eid}", headers=H).status_code == 404
    a.delete(f"/api/expenses/{eid}", headers=H)
    assert b.post(f"/api/expenses/{eid}/restore", headers=H).status_code == 404
    back = a.post(f"/api/expenses/{eid}/restore", headers=H).json()["expense"]
    # version 3 = created, deleted by its owner, restored by its owner; nothing the other user sent counted
    assert back["amount_paisa"] == 10000 and back["version"] == 3 and back["note"] == "private"


def test_the_404_is_identical_for_a_missing_row_and_someone_elses_row(api):
    a, b = api(), api()
    eid = add(a)["id"]
    mine_missing = b.delete("/api/expenses/424242", headers=H)
    theirs = b.delete(f"/api/expenses/{eid}", headers=H)
    assert (mine_missing.status_code, mine_missing.json()) == (theirs.status_code, theirs.json()) == (404, {"detail": {"code": "not_found"}})


def test_same_client_id_for_two_users_makes_two_rows(api):
    a, b = api(), api()
    cid = str(uuid.uuid4())
    ra = a.post("/api/expenses", json=body(client_id=cid), headers=H)
    rb = b.post("/api/expenses", json=body(client_id=cid), headers=H)
    assert ra.status_code == rb.status_code == 201 and ra.json()["expense"]["id"] != rb.json()["expense"]["id"]


# ── per-user write limit ─────────────────────────────────────────────────────

def test_writes_are_limited_per_user_and_reads_are_not(api, monkeypatch):
    from app.routers import expenses
    from app.services.rate_limit import SlidingWindowLimiter
    monkeypatch.setattr(expenses, "write_limiter", SlidingWindowLimiter(3, 60))
    a, b = api(), api()
    for _ in range(3):
        assert a.post("/api/expenses", json=body(), headers=H).status_code == 201
    r = a.post("/api/expenses", json=body(), headers=H)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1 and r.json()["detail"]["code"] == "rate_limited"
    assert a.delete("/api/expenses/1", headers=H).status_code == 429
    assert a.patch("/api/expenses/1", json={"version": 1, "note": "x"}, headers=H).status_code == 429
    assert a.get("/api/expenses?month=2026-10").status_code == 200                # reads are free
    assert b.post("/api/expenses", json=body(), headers=H).status_code == 201     # another user is not affected
    assert len(a.get("/api/expenses?month=2026-10").json()["items"]) == 3          # the blocked call wrote nothing


def test_a_missing_header_or_login_does_not_use_up_the_budget(api, monkeypatch):
    from app.routers import expenses
    from app.services.rate_limit import SlidingWindowLimiter
    monkeypatch.setattr(expenses, "write_limiter", SlidingWindowLimiter(2, 60))
    c = api()
    for _ in range(5):
        assert c.post("/api/expenses", json=body()).status_code == 403
    assert c.post("/api/expenses", json=body(), headers=H).status_code == 201


def test_default_limit_is_sixty_a_minute(api):
    from app.routers import expenses
    assert expenses.WRITES_PER_MINUTE == 60 and expenses.write_limiter.limit == 60 and expenses.write_limiter.window == 60


# ── privacy: expense text never reaches the logs ─────────────────────────────

def test_note_text_is_not_logged(api, caplog):
    caplog.set_level(logging.DEBUG)
    c = api()
    e = add(c, note=NOTE)
    c.patch(f"/api/expenses/{e['id']}", json={"version": 1, "note": NOTE + " 2"}, headers=H)
    c.patch(f"/api/expenses/{e['id']}", json={"version": 1, "note": NOTE + " 3"}, headers=H)       # 409
    c.post("/api/expenses", json=body(amount="bad", note=NOTE), headers=H)                          # 422
    c.delete(f"/api/expenses/{e['id']}", headers=H)
    assert "SECRET-NOTE" not in caplog.text


def test_error_bodies_do_not_echo_the_note(api):
    c = api()
    r = c.post("/api/expenses", json=body(amount="bad", note=NOTE), headers=H)
    assert "SECRET-NOTE" not in r.text


# ── changing the category (what the edit sheet sends) ────────────────────────

def test_update_with_only_a_subcategory_moves_the_category_and_flag(api):
    c = api()
    e = add(c, subcategory_slug="food.groceries")
    out = c.patch(f"/api/expenses/{e['id']}", json={"version": 1, "subcategory_slug": "financial.markup_interest"}, headers=H).json()
    assert out["category_slug"] == "financial" and out["subcategory_slug"] == "financial.markup_interest" and out["shariah_flag"] == "riba"
    back = c.patch(f"/api/expenses/{e['id']}", json={"version": 2, "category_slug": "other"}, headers=H).json()
    assert back["category_slug"] == "other" and back["subcategory_slug"] is None and back["shariah_flag"] is None


def test_update_with_a_mismatched_pair_is_422(api):
    c = api()
    e = add(c)
    r = c.patch(f"/api/expenses/{e['id']}", json={"version": 1, "category_slug": "transport", "subcategory_slug": "food.groceries"}, headers=H)
    assert r.status_code == 422


def test_every_user_can_delete_and_undo_their_own_rows(api):
    """The second user's ids and rows work the same way (guards against a hard-coded user in the routes)."""
    a, b = api(), api()
    add(a, note="a's row")
    mine = add(b, amount="40", note="b's row")
    assert b.get("/api/expenses/summary?month=2026-10").json()["total_paisa"] == 4000
    assert b.delete(f"/api/expenses/{mine['id']}", headers=H).status_code == 200
    back = b.post(f"/api/expenses/{mine['id']}/restore", headers=H).json()["expense"]
    assert back["id"] == mine["id"] and back["note"] == "b's row"
    assert [i["note"] for i in b.get("/api/expenses?month=2026-10").json()["items"]] == ["b's row"]
    assert [i["note"] for i in a.get("/api/expenses?month=2026-10").json()["items"]] == ["a's row"]
