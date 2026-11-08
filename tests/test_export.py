"""Data export (GET /api/expenses/export): the user's rows only, every row they still have, safe CSV. Offline."""
import csv
import io
import json
import re
import uuid
from datetime import timedelta

import pytest

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


def export_csv_rows(c):
    r = c.get("/api/expenses/export", params={"format": "csv"})
    assert r.status_code == 200, r.text
    text = r.content.decode("utf-8")
    assert text.startswith("\ufeff")
    return list(csv.DictReader(io.StringIO(text[1:], newline="")))


# ── who may export ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("fmt", ["json", "csv"])
def test_export_needs_login(api, fmt):
    r = api(login=False).get("/api/expenses/export", params={"format": fmt})
    assert r.status_code == 401


def test_export_works_with_a_bearer_token_too(api):
    c = api()
    add(c, note="n1")
    token = c.cookies.get("access_token")
    r = api(login=False).get("/api/expenses/export", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["count"] == 1


# ── scoping: only the caller's rows ──────────────────────────────────────────────

def test_export_contains_only_the_callers_rows(api):
    a, b = api(), api()
    mine = [add(a, note=f"A-NOTE-{i}-zq81") for i in range(3)]
    theirs = [add(b, note=f"B-NOTE-{i}-zq81") for i in range(2)]
    for client, own, other, tag in ((a, mine, theirs, "B-NOTE"), (b, theirs, mine, "A-NOTE")):
        r = client.get("/api/expenses/export", params={"format": "json"})
        data = r.json()
        assert sorted(x["id"] for x in data["expenses"]) == sorted(x["id"] for x in own)
        assert data["count"] == len(own)
        assert tag not in r.text
        assert not ({x["id"] for x in other} & {x["id"] for x in data["expenses"]})
    csv_rows = export_csv_rows(a)
    assert sorted(int(x["id"]) for x in csv_rows) == sorted(x["id"] for x in mine)
    assert "B-NOTE" not in json.dumps(csv_rows)


def test_export_has_no_user_id_field(api):
    c = api()
    add(c)
    row = export_json(c)["expenses"][0]
    assert "user_id" not in row
    assert "user_id" not in ef.CSV_COLUMNS


# ── which rows: everything not purged ────────────────────────────────────────────

def test_soft_deleted_rows_are_included_until_they_are_purged(api, clock):
    c = api()
    keep = add(c, note="keep")
    gone = add(c, note="deleted-but-not-purged")
    assert c.delete(f"/api/expenses/{gone['id']}", headers=H).status_code == 200
    rows = {x["id"]: x for x in export_json(c)["expenses"]}
    assert set(rows) == {keep["id"], gone["id"]}
    assert rows[keep["id"]]["deleted_at"] is None
    assert rows[gone["id"]]["deleted_at"] is not None
    csv_rows = {int(x["id"]): x for x in export_csv_rows(c)}
    assert csv_rows[keep["id"]]["deleted_at"] == "" and csv_rows[gone["id"]]["deleted_at"] != ""


def test_purged_rows_are_not_exported(api, clock):
    c = api()
    keep = add(c, note="keep")
    gone = add(c, note="purge-me")
    c.delete(f"/api/expenses/{gone['id']}", headers=H)
    clock["now"] += timedelta(days=31)
    with api.Session() as db:
        assert es.purge_deleted_expenses(db) == 1
    assert [x["id"] for x in export_json(c)["expenses"]] == [keep["id"]]


def test_notes_are_included_and_urdu_survives_both_formats(api):
    c = api()
    note = "دودھ اور دہی, Ali ko udhaar"
    add(c, note=note)
    assert export_json(c)["expenses"][0]["note"] == note
    assert export_csv_rows(c)[0]["note"] == note


def test_rows_are_oldest_first(api):
    c = api()
    for day in ("2026-10-25", "2026-10-02", "2026-10-30"):
        add(c, occurred_on=day)
    assert [x["occurred_on"] for x in export_json(c)["expenses"]] == ["2026-10-02", "2026-10-25", "2026-10-30"]


def test_empty_export_is_valid_in_both_formats(api):
    c = api()
    data = export_json(c)
    assert data["count"] == 0 and data["expenses"] == []
    r = c.get("/api/expenses/export", params={"format": "csv"})
    assert r.content.decode("utf-8-sig").strip() == ",".join(ef.CSV_COLUMNS)


def test_export_does_not_change_anything(api):
    c = api()
    row = add(c, note="same")
    export_json(c)
    export_csv_rows(c)
    again = c.get("/api/expenses", params={"month": "2026-10"}).json()["items"][0]
    assert again["version"] == row["version"] and again["updated_at"] == row["updated_at"]


# ── money is exact ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("amount, paisa, rupees", [
    ("0.01", 1, "0.01"), ("1,250.50", 125050, "1250.50"), ("7", 700, "7.00"),
    ("1,00,000", 10000000, "100000.00"), ("9999999999.99", 999999999999, "9999999999.99"),
])
def test_amounts_are_exact(api, amount, paisa, rupees):
    c = api()
    add(c, amount=amount)
    j = export_json(c)["expenses"][0]
    assert (j["amount_paisa"], j["amount_rupees"]) == (paisa, rupees)
    k = export_csv_rows(c)[0]
    assert (k["amount_paisa"], k["amount_rupees"]) == (str(paisa), rupees)


def test_amount_rupees_unit():
    assert ef.amount_rupees(0) == "0.00" and ef.amount_rupees(5) == "0.05" and ef.amount_rupees(10 ** 12) == "10000000000.00"
    for bad in (-1, 1.5, "100", True, None):
        with pytest.raises(ValueError):
            ef.amount_rupees(bad)


# ── CSV safety: spreadsheet formula injection ────────────────────────────────────

@pytest.mark.parametrize("note", ["=1+1", "+1+1", "-2+3", "@SUM(A1:A9)", '=HYPERLINK("http://x","click")', "=cmd|' /C calc'!A0"])
def test_a_note_that_looks_like_a_formula_is_neutralised_in_csv(api, note):
    c = api()
    add(c, note=note)
    cell = export_csv_rows(c)[0]["note"]
    assert cell == "'" + note
    assert export_json(c)["expenses"][0]["note"] == note          # JSON keeps the original text


@pytest.mark.parametrize("raw, safe", [
    ("=1+1", "'=1+1"), ("+1", "'+1"), ("-1", "'-1"), ("@x", "'@x"),
    ("\t=1", "'\t=1"), ("\r=1", "'\r=1"), ("\n=1", "'\n=1"),
    (" =1+1", "' =1+1"), ("\u00a0=1+1", "'\u00a0=1+1"),
    ("rent", "rent"), ("دودھ", "دودھ"), ("12=3", "12=3"), ("a-b", "a-b"), ("", ""), ("2026-10-30", "2026-10-30"),
])
def test_csv_safe_unit(raw, safe):
    assert ef.csv_safe(raw) == safe


def test_csv_commas_quotes_and_dates_round_trip(api):
    c = api()
    add(c, note='says "hi", then left', amount="12.34", occurred_on="2026-10-05")
    row = export_csv_rows(c)[0]
    assert row["note"] == 'says "hi", then left' and row["occurred_on"] == "2026-10-05"


def test_csv_and_json_agree_row_by_row(api):
    c = api()
    for i in range(6):
        add(c, note=f"n{i}", amount=str(10 + i), occurred_on=f"2026-10-{10 + i:02d}")
    c.delete(f"/api/expenses/{export_json(c)['expenses'][2]['id']}", headers=H)
    j = export_json(c)["expenses"]
    k = export_csv_rows(c)
    assert len(j) == len(k) == 6
    for jr, kr in zip(j, k):
        for column in ("id", "client_id", "occurred_on", "amount_rupees", "amount_paisa", "category", "subcategory", "payment_method", "source", "version"):
            assert str(jr[column]) == kr[column], column
        assert (jr["deleted_at"] or "") == kr["deleted_at"]
        assert str(jr["shariah_flag_dismissed"]).lower() == kr["shariah_flag_dismissed"]


def test_csv_layout(api):
    c = api()
    add(c)
    raw = c.get("/api/expenses/export", params={"format": "csv"}).content.decode("utf-8")
    assert raw.startswith("\ufeff" + ",".join(ef.CSV_COLUMNS) + "\r\n")
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


def test_json_envelope(api, clock):
    c = api()
    add(c)
    data = export_json(c)
    assert data["format_version"] == 1 and data["currency"] == "PKR" and data["count"] == 1
    assert data["exported_at"].startswith("2026-10-31T07:00:00")


# ── limiter ──────────────────────────────────────────────────────────────────────

def test_export_is_limited_per_user(api):
    a, b = api(), api()
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


def test_service_rejects_bad_arguments(db_session, make_user):
    uid = make_user()
    for bad in (0, "1", None, True, 1.5):
        with pytest.raises(TypeError):
            list(es.export_expenses(db_session, bad))
    for bad in (0, -1, True, "5"):
        with pytest.raises(es.InvalidInput):
            list(es.export_expenses(db_session, uid, batch_size=bad))
    assert list(es.export_expenses(db_session, make_user())) == []
