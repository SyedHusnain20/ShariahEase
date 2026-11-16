"""
Data export (GET /api/expenses/export): a small, friendly file for people who just want to see their spending.
Six fields only (ID, date, amount in rupees, category, sub-category, source), the caller's current expenses only. Offline.
"""
import csv
import io
import json
import re
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.database import models
from app.routers import expenses as expenses_router
from app.services import expense_service as es
from app.services import export_format as ef
from tests.test_expense_api import H, add, api, body  # noqa: F401  (api is a fixture)


@pytest.fixture(autouse=True)
def fresh_export_limiter():
    expenses_router.export_limiter.reset()
    yield
    expenses_router.export_limiter.reset()


def export_json(c, **params):
    r = c.get("/api/expenses/export", params={"format": "json", **params})
    assert r.status_code == 200, r.text
    return r.json()


def export_csv_text(c):
    r = c.get("/api/expenses/export", params={"format": "csv"})
    assert r.status_code == 200, r.text
    text_ = r.content.decode("utf-8")
    assert text_.startswith("\ufeff")
    return text_[1:]


def export_csv_rows(c):
    return list(csv.DictReader(io.StringIO(export_csv_text(c), newline="")))


def user_id_of(db, email):
    return db.execute(text("SELECT id FROM users WHERE email = :e"), {"e": email}).scalar()


# ── who may export ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("fmt", ["json", "csv"])
def test_export_needs_login(api, fmt):
    assert api(login=False).get("/api/expenses/export", params={"format": fmt}).status_code == 401


def test_export_works_with_a_bearer_token_too(api):
    c = api()
    add(c)
    token = c.cookies.get("access_token")
    r = api(login=False).get("/api/expenses/export", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["count"] == 1


# ── exactly the six fields ───────────────────────────────────────────────────────

def test_the_json_has_exactly_the_six_fields(api):
    c = api()
    add(c, note="something private", payment_method="bank_transfer")
    row = export_json(c)["expenses"][0]
    assert list(row) == ["id", "date", "amount_rupees", "category", "sub_category", "source"] == ef.JSON_KEYS


def test_the_csv_has_exactly_the_six_columns_in_plain_words(api):
    c = api()
    add(c, note="something private", payment_method="bank_transfer")
    first_line = export_csv_text(c).split("\r\n")[0]
    assert first_line == "ID,Date,Amount (Rs),Category,Sub-category,Source" == ",".join(ef.CSV_HEADERS)
    assert list(export_csv_rows(c)[0]) == ef.CSV_HEADERS


def test_notes_payment_method_and_internal_details_never_leave_in_the_file(api):
    c = api()
    row = add(c, note="PRIVATE-NOTE-zq81 دودھ", payment_method="bank_transfer", amount="1,250.50")
    raw_json = c.get("/api/expenses/export", params={"format": "json"}).text
    raw_csv = export_csv_text(c)
    for blob in (raw_json, raw_csv):
        for forbidden in ("PRIVATE-NOTE", "zq81", "دودھ", "bank_transfer", row["client_id"], "paisa", "currency", "PKR", "version",
                          "created_at", "updated_at", "deleted_at", "client_id", "user_id", "payment", "shariah", "note"):
            assert forbidden.lower() not in blob.lower(), forbidden


def test_the_service_never_even_reads_the_note_column(db_session, make_user):
    uid = make_user()
    es.add_expenses(db_session, uid, [{"client_id": uuid.uuid4(), "amount_paisa": 100, "occurred_on": "2026-10-30",
                                       "subcategory_slug": "food.groceries", "note": "secret words"}])
    [row] = list(es.export_expenses(db_session, uid))
    assert set(es.ExportRow.model_fields) == {"id", "occurred_on", "amount_paisa", "category", "sub_category", "source"}
    assert "secret words" not in row.model_dump_json()


# ── scoping: only the caller's rows ──────────────────────────────────────────────

def test_export_contains_only_the_callers_rows(api):
    a, b = api("a@example.com"), api("b@example.com")
    mine = [add(a, note=f"A-NOTE-{i}") for i in range(3)]
    theirs = [add(b, note=f"B-NOTE-{i}") for i in range(2)]
    for client, own, other in ((a, mine, theirs), (b, theirs, mine)):
        data = client.get("/api/expenses/export", params={"format": "json"}).json()
        assert sorted(x["id"] for x in data["expenses"]) == sorted(x["id"] for x in own)
        assert data["count"] == len(own)
        assert not ({x["id"] for x in other} & {x["id"] for x in data["expenses"]})
    assert sorted(int(x["ID"]) for x in export_csv_rows(a)) == sorted(x["id"] for x in mine)
    assert sorted(int(x["ID"]) for x in export_csv_rows(b)) == sorted(x["id"] for x in theirs)


# ── which rows: the expenses the user has now ────────────────────────────────────

def test_deleted_expenses_are_not_in_the_file_and_come_back_when_restored(api):
    c = api()
    keep = add(c, amount="10")
    gone = add(c, amount="20")
    assert c.delete(f"/api/expenses/{gone['id']}", headers=H).status_code == 200
    assert [x["id"] for x in export_json(c)["expenses"]] == [keep["id"]]
    assert [int(x["ID"]) for x in export_csv_rows(c)] == [keep["id"]]
    assert c.post(f"/api/expenses/{gone['id']}/restore", headers=H).status_code == 200
    assert sorted(x["id"] for x in export_json(c)["expenses"]) == sorted([keep["id"], gone["id"]])


def test_a_purged_expense_is_still_not_in_the_file(api, clock):
    c = api()
    keep = add(c)
    gone = add(c)
    c.delete(f"/api/expenses/{gone['id']}", headers=H)
    clock["now"] += timedelta(days=31)
    with api.Session() as db:
        assert es.purge_deleted_expenses(db) == 1
    assert [x["id"] for x in export_json(c)["expenses"]] == [keep["id"]]


def test_rows_are_oldest_first(api):
    c = api()
    for day in ("2026-10-25", "2026-10-02", "2026-10-30"):
        add(c, occurred_on=day)
    assert [x["date"] for x in export_json(c)["expenses"]] == ["2026-10-02", "2026-10-25", "2026-10-30"]
    assert [x["Date"] for x in export_csv_rows(c)] == ["2026-10-02", "2026-10-25", "2026-10-30"]


def test_empty_export_is_valid_in_both_formats(api):
    c = api()
    data = export_json(c)
    assert data["count"] == 0 and data["expenses"] == []
    assert c.get("/api/expenses/export", params={"format": "csv"}).content.decode("utf-8-sig").strip() == ",".join(ef.CSV_HEADERS)


def test_export_does_not_change_anything(api):
    c = api()
    row = add(c)
    export_json(c)
    export_csv_rows(c)
    again = c.get("/api/expenses", params={"month": "2026-10"}).json()["items"][0]
    assert again["version"] == row["version"] and again["updated_at"] == row["updated_at"]


# ── category, sub-category: names a person can read ──────────────────────────────

def test_category_and_sub_category_are_names_not_codes(api):
    c = api()
    add(c, subcategory_slug="food.groceries")
    names = {sub["slug"]: (cat["name_en"], sub["name_en"]) for cat in c.get("/api/expenses/categories").json()
             for sub in cat["subcategories"]}
    cat_name, sub_name = names["food.groceries"]
    j = export_json(c)["expenses"][0]
    assert (j["category"], j["sub_category"]) == (cat_name, sub_name)
    assert "." not in j["category"] and "food.groceries" not in json.dumps(j)
    k = export_csv_rows(c)[0]
    assert (k["Category"], k["Sub-category"]) == (cat_name, sub_name)


def test_an_expense_with_only_a_category_has_an_empty_sub_category(api):
    c = api()
    cat = c.get("/api/expenses/categories").json()[0]
    add(c, subcategory_slug=None, category_slug=cat["slug"])
    assert export_json(c)["expenses"][0]["sub_category"] is None
    assert export_csv_rows(c)[0]["Sub-category"] == ""
    assert export_json(c)["expenses"][0]["category"] == cat["name_en"]


# ── source: manual or chatbot ────────────────────────────────────────────────────

@pytest.mark.parametrize("stored, shown", [("manual", "manual"), ("pwa_sync", "manual"), ("ai_chat", "chatbot"), ("whatsapp", "chatbot")])
def test_source_is_shown_as_manual_or_chatbot(db_session, make_user, stored, shown):
    uid = make_user()
    db_session.add(models.Expense(user_id=uid, client_id=uuid.uuid4(), amount_paisa=100, occurred_on=date(2026, 10, 30),
                                  category_id=1, source=stored))
    db_session.commit()
    [row] = list(es.export_expenses(db_session, uid))
    assert row.source == stored
    assert ef.to_json([row], es._utcnow())                              # renders
    assert json.loads(ef.to_json([row], es._utcnow()))["expenses"][0]["source"] == shown
    assert list(csv.DictReader(io.StringIO(ef.to_csv([row])[1:], newline="")))[0]["Source"] == shown


def test_a_manual_expense_made_in_the_app_says_manual(api):
    c = api()
    add(c)
    assert export_json(c)["expenses"][0]["source"] == "manual" and export_csv_rows(c)[0]["Source"] == "manual"


def test_every_source_the_database_allows_has_a_label():
    check = " ".join(str(c.sqltext) for c in models.Expense.__table__.constraints if c.__class__.__name__ == "CheckConstraint"
                     and "source" in str(c.sqltext))
    allowed = set(re.findall(r"'(\w+)'", check))
    assert allowed == {"manual", "ai_chat", "whatsapp", "pwa_sync"}
    assert set(ef.SOURCE_LABEL) == allowed, "a new source needs an export label (manual or chatbot)"
    assert set(ef.SOURCE_LABEL.values()) == {"manual", "chatbot"}


def test_an_unknown_source_is_an_error_not_a_guess():
    with pytest.raises(ValueError):
        ef.source_label("telepathy")


# ── money is exact ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("amount, rupees", [
    ("0.01", "0.01"), ("1,250.50", "1250.50"), ("7", "7.00"), ("1,00,000", "100000.00"), ("9999999999.99", "9999999999.99"),
])
def test_amounts_are_exact(api, amount, rupees):
    c = api()
    add(c, amount=amount)
    assert export_json(c)["expenses"][0]["amount_rupees"] == rupees
    assert export_csv_rows(c)[0]["Amount (Rs)"] == rupees


def test_amount_rupees_unit():
    assert ef.amount_rupees(0) == "0.00" and ef.amount_rupees(5) == "0.05" and ef.amount_rupees(10 ** 12) == "10000000000.00"
    for bad in (-1, 1.5, "100", True, None):
        with pytest.raises(ValueError):
            ef.amount_rupees(bad)


# ── CSV safety (defence in depth: no user text is in the file any more) ───────────

@pytest.mark.parametrize("raw, safe", [
    ("=1+1", "'=1+1"), ("+1", "'+1"), ("-1", "'-1"), ("@x", "'@x"),
    ("\t=1", "'\t=1"), ("\r=1", "'\r=1"), ("\n=1", "'\n=1"),
    (" =1+1", "' =1+1"), ("\u00a0=1+1", "'\u00a0=1+1"),
    ("Groceries", "Groceries"), ("دودھ", "دودھ"), ("12=3", "12=3"), ("a-b", "a-b"), ("", ""), ("2026-10-30", "2026-10-30"),
])
def test_csv_safe_unit(raw, safe):
    assert ef.csv_safe(raw) == safe


@pytest.mark.parametrize("name", ["=HYPERLINK(\"http://x\",\"click\")", "+1+1", "-2+3", "@SUM(A1:A9)"])
def test_a_category_name_that_looks_like_a_formula_is_neutralised_in_csv(name):
    row = es.ExportRow(id=1, occurred_on=date(2026, 10, 30), amount_paisa=100, category=name, sub_category=name, source="manual")
    cells = list(csv.DictReader(io.StringIO(ef.to_csv([row])[1:], newline="")))[0]
    assert cells["Category"] == "'" + name and cells["Sub-category"] == "'" + name
    assert json.loads(ef.to_json([row], es._utcnow()))["expenses"][0]["category"] == name      # JSON keeps the original


def test_csv_and_json_agree_row_by_row(api):
    c = api()
    for i in range(6):
        add(c, amount=str(10 + i), occurred_on=f"2026-10-{10 + i:02d}")
    j = export_json(c)["expenses"]
    k = export_csv_rows(c)
    assert len(j) == len(k) == 6
    for jr, kr in zip(j, k):
        assert [str(jr[key]) for key in ef.JSON_KEYS] == [kr[h] for h in ef.CSV_HEADERS]


def test_csv_layout(api):
    c = api()
    add(c)
    raw = c.get("/api/expenses/export", params={"format": "csv"}).content.decode("utf-8")
    assert raw.startswith("\ufeff" + ",".join(ef.CSV_HEADERS) + "\r\n")
    assert raw.endswith("\r\n")


# ── response headers and parameters ──────────────────────────────────────────────

def test_headers_json_and_csv(api, clock):
    c = api()
    add(c)
    for fmt, mime in (("json", "application/json"), ("csv", "text/csv")):
        r = c.get("/api/expenses/export", params={"format": fmt})
        assert r.headers["content-type"].startswith(mime) and "utf-8" in r.headers["content-type"]
        assert r.headers["content-disposition"] == f'attachment; filename="shariahease-expenses-2026-10-31.{fmt}"'
        assert r.headers["cache-control"] == "no-store"


def test_format_defaults_to_json(api):
    c = api()
    add(c)
    r = c.get("/api/expenses/export")
    assert r.status_code == 200 and r.json()["count"] == 1


@pytest.mark.parametrize("fmt", ["xml", "CSV", "", "json;csv", "../x"])
def test_unknown_format_is_422(api, fmt):
    r = api().get("/api/expenses/export", params={"format": fmt})
    assert r.status_code == 422
    assert r.json()["detail"] == {"code": "invalid_input", "errors": [{"field": "format", "problem": "expected json or csv"}]}


def test_json_envelope_is_small(api, clock):
    c = api()
    add(c)
    data = export_json(c)
    assert list(data) == ["exported_at", "count", "expenses"] and data["count"] == 1
    assert data["exported_at"].startswith("2026-10-31T07:00:00")


# ── limiter ──────────────────────────────────────────────────────────────────────

def test_export_is_limited_per_user(api):
    a, b = api("l1@example.com"), api("l2@example.com")
    for _ in range(expenses_router.EXPORTS_PER_WINDOW):
        assert a.get("/api/expenses/export").status_code == 200
    r = a.get("/api/expenses/export")
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    assert r.json()["detail"]["code"] == "rate_limited"
    assert b.get("/api/expenses/export").status_code == 200       # another user is not affected


def test_a_refused_format_does_not_use_up_the_quota(api):
    c = api()
    for _ in range(expenses_router.EXPORTS_PER_WINDOW + 3):
        assert c.get("/api/expenses/export", params={"format": "xml"}).status_code == 422
    assert c.get("/api/expenses/export").status_code == 200


# ── the service function itself ──────────────────────────────────────────────────

def test_service_batches_cover_every_row_once_in_order(db_session, make_user, clock):
    uid = make_user()
    for i in range(7):                                             # several rows share a date
        es.add_expenses(db_session, uid, [{"client_id": uuid.uuid4(), "amount_paisa": 100 + i,
                                           "occurred_on": f"2026-10-{20 + i // 3:02d}", "subcategory_slug": "food.groceries"}])
    whole = [r.id for r in es.export_expenses(db_session, uid, batch_size=1000)]
    for size in (1, 2, 3, 7, 8):
        assert [r.id for r in es.export_expenses(db_session, uid, batch_size=size)] == whole
    assert len(whole) == len(set(whole)) == 7


def test_batches_skip_deleted_rows_without_losing_or_repeating_others(db_session, make_user, clock):
    uid = make_user()
    ids = [es.add_expenses(db_session, uid, [{"client_id": uuid.uuid4(), "amount_paisa": 100 + i, "occurred_on": "2026-10-20",
                                              "subcategory_slug": "food.groceries"}])[0].expense.id for i in range(6)]
    es.delete_expenses(db_session, uid, [ids[1], ids[2]])
    for size in (1, 2, 3, 10):
        assert [r.id for r in es.export_expenses(db_session, uid, batch_size=size)] == [ids[0], ids[3], ids[4], ids[5]]


def test_service_rejects_bad_arguments(db_session, make_user):
    uid = make_user()
    for bad in (0, "1", None, True, 1.5):
        with pytest.raises(TypeError):
            list(es.export_expenses(db_session, bad))
    for bad in (0, -1, True, "5"):
        with pytest.raises(es.InvalidInput):
            list(es.export_expenses(db_session, uid, batch_size=bad))
    assert list(es.export_expenses(db_session, make_user())) == []
