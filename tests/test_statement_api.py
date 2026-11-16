"""Statement routes and the PDF: login, scoping, window parameters, limits, privacy of notes, dismissal over HTTP."""
import re
import uuid

import pytest

from tests.test_expense_api import H, api  # noqa: F401  (the api fixture: one app, several users, separate cookie jars)

NOTE = "SECRET-NOTE-zq81 masjid ko diya"


def give(c, sub="charity.zakat", amount="1000", on="2026-03-10", **kw):
    data = {"client_id": str(uuid.uuid4()), "amount": amount, "occurred_on": on, "subcategory_slug": sub}
    data.update(kw)
    r = c.post("/api/expenses", json=data, headers=H)
    assert r.status_code in (200, 201), r.text
    return r.json()["expense"]


@pytest.fixture(autouse=True)
def _reset_statement_limiter():
    from app.routers import expenses
    expenses.statement_limiter.reset()
    yield
    expenses.statement_limiter.reset()


# ── login ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/api/expenses/statement", "/api/expenses/statement?year=2026",
                                  "/api/expenses/statement.pdf", "/api/expenses/statement.pdf?lang=ur"])
def test_statement_routes_need_login(api, path):
    assert api(login=False).get(path).status_code == 401


# ── JSON ──────────────────────────────────────────────────────────────────────

def test_the_statement_for_a_year(api):
    c = api()
    give(c, "charity.zakat", "25,000", "2026-03-10")
    give(c, "charity.sadaqah", "1250.50", "2026-05-01", note=NOTE)
    give(c, "food.groceries", "999", "2026-05-02")
    give(c, "charity.zakat", "700", "2025-12-31")
    r = c.get("/api/expenses/statement?year=2026")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    d = r.json()
    assert (d["date_from"], d["date_to"], d["currency"]) == ("2026-01-01", "2027-01-01", "PKR")
    assert (d["total_paisa"], d["count"]) == (2_625_050, 2)
    assert {t["key"]: t["total_paisa"] for t in d["types"]} == {"zakat": 2_500_000, "sadaqah": 125_050, "fitrana": 0, "other": 0}
    assert [e["amount_paisa"] for e in d["entries"]] == [2_500_000, 125_050]
    assert d["entries"][1]["note"] == NOTE                                        # on screen, to its own owner
    assert d["zakat_check"] is None and d["entries_truncated"] is False


def test_the_default_year_is_the_users_current_year(api, clock):
    c = api()
    give(c, on="2026-10-30")
    r = c.get("/api/expenses/statement")                                           # the frozen clock says 31 Oct 2026
    assert r.status_code == 200 and r.json()["date_from"] == "2026-01-01" and r.json()["count"] == 1


def test_an_explicit_window_is_start_inclusive_end_exclusive(api):
    c = api()
    for d in ("2026-03-01", "2026-03-15", "2026-03-31", "2026-04-01"):
        give(c, on=d, amount="1")
    r = c.get("/api/expenses/statement?date_from=2026-03-15&date_to=2026-04-01")
    assert r.status_code == 200 and r.json()["count"] == 2


@pytest.mark.parametrize("query", [
    "year=1999", "year=2101", "year=abc", "year=2026&date_from=2026-01-01&date_to=2026-02-01",
    "date_from=2026-01-01", "date_to=2026-02-01", "date_from=2026-02-01&date_to=2026-01-01",
    "date_from=1990-01-01&date_to=1991-01-01", "date_from=nope&date_to=2026-01-01",
])
def test_bad_windows_are_refused(api, query):
    r = api().get("/api/expenses/statement?" + query)
    assert r.status_code == 422, (query, r.status_code)


def test_one_users_statement_never_shows_another_users_giving(api):
    a, b = api(), api()
    give(a, amount="111")
    give(b, amount="222", note=NOTE)
    mine = a.get("/api/expenses/statement?year=2026")
    assert mine.json()["total_paisa"] == 11_100 and [e["amount_paisa"] for e in mine.json()["entries"]] == [11_100]
    assert NOTE not in mine.text
    theirs = b.get("/api/expenses/statement?year=2026")
    assert theirs.json()["total_paisa"] == 22_200 and NOTE in theirs.text


def test_a_deleted_expense_leaves_the_statement(api):
    c = api()
    e = give(c)
    assert c.get("/api/expenses/statement?year=2026").json()["count"] == 1
    assert c.delete(f"/api/expenses/{e['id']}", headers=H).status_code == 200
    assert c.get("/api/expenses/statement?year=2026").json()["count"] == 0


def test_the_zakat_calculator_comparison_appears_after_a_saved_calculation(api):
    from datetime import datetime, timezone
    from app.database.models import User, ZakatRecord
    c = api()
    give(c, "charity.zakat", "25,000", "2026-03-10")
    db = api.Session()
    uid = db.query(User).one().id
    db.add(ZakatRecord(user_id=uid, user_name="U", total_assets=1, total_deductions=0, zakatable_wealth=1, nisab_threshold=1,
                       zakat_due=30000.0, is_zakat_due=True, calculated_at=datetime(2026, 6, 1, 10, tzinfo=timezone.utc)))
    db.commit()
    db.close()
    z = c.get("/api/expenses/statement?year=2026").json()["zakat_check"]
    assert (z["zakat_due_paisa"], z["zakat_paid_paisa"], z["remaining_paisa"]) == (3_000_000, 2_500_000, 500_000)


def test_statement_requests_are_limited_per_user(api):
    from app.routers import expenses
    c, other = api(), api()
    for _ in range(expenses.STATEMENTS_PER_WINDOW):
        assert c.get("/api/expenses/statement?year=2026").status_code == 200
    r = c.get("/api/expenses/statement?year=2026")
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    assert c.get("/api/expenses/statement.pdf?year=2026").status_code == 429          # the PDF shares the allowance
    assert other.get("/api/expenses/statement?year=2026").status_code == 200          # another user is not affected


def test_a_refused_window_does_not_use_up_the_allowance(api):
    from app.routers import expenses
    c = api()
    for _ in range(expenses.STATEMENTS_PER_WINDOW + 3):
        assert c.get("/api/expenses/statement?year=1999").status_code == 422
    assert c.get("/api/expenses/statement?year=2026").status_code == 200


# ── PDF ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("lang", ["en", "ur"])
def test_the_pdf_is_a_pdf_and_is_not_cached(api, lang):
    c = api()
    give(c, "charity.sadaqah", "500", note=NOTE)
    r = c.get(f"/api/expenses/statement.pdf?year=2026&lang={lang}")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF-") and r.content.rstrip().endswith(b"%%EOF")
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["content-disposition"] == f'attachment; filename="shariahease-zakat-sadaqah-2026-{lang}.pdf"'


def test_the_default_language_is_english(api):
    r = api().get("/api/expenses/statement.pdf?year=2026")
    assert r.status_code == 200 and "-en.pdf" in r.headers["content-disposition"]


@pytest.mark.parametrize("lang", ["fr", "EN", "", "en;ur", "ur "])
def test_an_unknown_language_is_refused(api, lang):
    assert api().get("/api/expenses/statement.pdf", params={"year": 2026, "lang": lang}).status_code == 422


def test_a_custom_window_names_the_file_by_its_dates(api):
    r = api().get("/api/expenses/statement.pdf?date_from=2026-03-15&date_to=2026-04-01")
    assert r.headers["content-disposition"] == 'attachment; filename="shariahease-zakat-sadaqah-2026-03-15_to_2026-03-31-en.pdf"'


def test_the_pdf_never_contains_notes_and_is_scoped_to_the_caller(api, monkeypatch):
    import reportlab.rl_config as rl
    monkeypatch.setattr(rl, "pageCompression", 0)                                      # readable streams for the check
    a, b = api(), api()
    give(a, "charity.sadaqah", "500", note=NOTE)
    give(b, "charity.zakat", "7,654,321", note="OTHER-USER-NOTE-ab12")
    pdf = a.get("/api/expenses/statement.pdf?year=2026&lang=en").content
    assert b"Sadaqah" in pdf and b"500.00" in pdf                                       # the positive control: text is readable
    assert b"SECRET-NOTE" not in pdf and b"OTHER-USER-NOTE" not in pdf
    assert b"7,654,321" not in pdf


def test_the_pdf_names_the_person_it_was_issued_to(api, monkeypatch):
    import reportlab.rl_config as rl
    monkeypatch.setattr(rl, "pageCompression", 0)
    pdf = api().get("/api/expenses/statement.pdf?year=2026&lang=en").content
    assert b"Amina" in pdf


# ── dismissal over HTTP ───────────────────────────────────────────────────────

def test_a_reminder_can_be_dismissed_and_shown_again_over_http(api):
    c = api()
    e = give(c, "food.groceries", "100", note="bank interest")
    assert (e["shariah_flag"], e["shariah_flag_dismissed"]) == ("riba", False)
    r = c.patch(f"/api/expenses/{e['id']}", json={"version": e["version"], "shariah_flag_dismissed": True}, headers=H)
    assert r.status_code == 200 and r.json()["shariah_flag_dismissed"] is True
    v = r.json()["version"]
    r = c.patch(f"/api/expenses/{e['id']}", json={"version": v, "amount": "250"}, headers=H)
    assert r.json()["shariah_flag_dismissed"] is True and r.json()["shariah_flag"] == "riba"      # still dismissed
    r = c.patch(f"/api/expenses/{e['id']}", json={"version": r.json()["version"], "shariah_flag_dismissed": False}, headers=H)
    assert r.json()["shariah_flag_dismissed"] is False


@pytest.mark.parametrize("bad", [1, "true", None, "yes"])
def test_dismissal_must_be_a_boolean_over_http(api, bad):
    c = api()
    e = give(c, "food.groceries", "100", note="interest")
    r = c.patch(f"/api/expenses/{e['id']}", json={"version": e["version"], "shariah_flag_dismissed": bad}, headers=H)
    assert r.status_code == 422


def test_dismissing_where_there_is_no_reminder_is_422(api):
    c = api()
    e = give(c, "food.groceries", "100", note="atta")
    r = c.patch(f"/api/expenses/{e['id']}", json={"version": e["version"], "shariah_flag_dismissed": True}, headers=H)
    assert r.status_code == 422 and r.json()["detail"]["errors"][0]["field"] == "shariah_flag_dismissed"


def test_nobody_can_dismiss_another_users_reminder_over_http(api):
    a, b = api(), api()
    e = give(a, "food.groceries", "100", note="interest")
    r = b.patch(f"/api/expenses/{e['id']}", json={"version": e["version"], "shariah_flag_dismissed": True}, headers=H)
    assert r.status_code == 404
    assert a.get("/api/expenses?month=2026-03").json()["items"][0]["shariah_flag_dismissed"] is False


def test_dismissing_needs_the_forgery_header(api):
    c = api()
    e = give(c, "food.groceries", "100", note="interest")
    r = c.patch(f"/api/expenses/{e['id']}", json={"version": e["version"], "shariah_flag_dismissed": True})
    assert r.status_code == 403


def test_a_stale_dismissal_is_409(api):
    c = api()
    e = give(c, "food.groceries", "100", note="interest")
    c.patch(f"/api/expenses/{e['id']}", json={"version": e["version"], "amount": "5"}, headers=H)
    r = c.patch(f"/api/expenses/{e['id']}", json={"version": e["version"], "shariah_flag_dismissed": True}, headers=H)
    assert r.status_code == 409 and r.json()["detail"]["current_version"] == 2
