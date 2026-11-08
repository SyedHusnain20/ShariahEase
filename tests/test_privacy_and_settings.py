"""The privacy page, the Settings page and the links to them. The privacy text is tied to the code: if a table, the purge
period or the guest-memory lifetime changes, these tests fail until the page is updated. Offline."""
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import pages
from app.services import expense_service as es
from app.services.guest_memory import guest_memory
from tests.test_account_deletion import SEEDERS, user_tables

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "frontend/static"


@pytest.fixture
def web(monkeypatch):
    monkeypatch.delenv("PRIVACY_CONTACT_EMAIL", raising=False)
    app = FastAPI()
    app.include_router(pages.router)
    from fastapi.staticfiles import StaticFiles
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
    return TestClient(app)


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


# ── the privacy page ─────────────────────────────────────────────────────────────

def test_privacy_page_is_public_and_says_the_important_things(web):
    r = web.get("/privacy")
    assert r.status_code == 200
    t = text_of(r.text)
    for phrase in ("never store card numbers", "CVV", "bcrypt", "Groq", "Neon", "Hugging Face", "DuckDuckGo", "LangSmith",
                   "WhatsApp", "Delete your account", "Download your expenses", "do not type card numbers", "Last updated: " + pages.PRIVACY_UPDATED):
        assert phrase.lower() in t.lower(), phrase


def test_privacy_page_mentions_every_table_that_holds_user_data(web):
    """If a new table with a user_id appears, the page must say what it holds (and the deletion test needs a seeder)."""
    html = web.get("/privacy").text
    stored = html[html.index('id="stored-h"'):html.index('aria-labelledby="not-h"')]      # the "What we store" section only
    page = text_of(stored).lower()
    keyword = {                     # table -> a phrase the "What we store" section must contain
        "zakat_records": "zakat", "screener_history": "screener", "chat_messages": "chat history",
        "refresh_tokens": "refresh token", "expenses": "expenses", "audit_log": "activity log",
    }
    assert set(keyword) == set(SEEDERS)                    # the same tables the deletion test covers
    for table, word in keyword.items():
        assert word in page, f"the 'What we store' section does not mention {table} ({word})"
    assert "your account" in page                         # the users table itself


def test_privacy_page_numbers_match_the_code(web):
    page = text_of(web.get("/privacy").text)
    assert f"it stays for {es.PURGE_AFTER_DAYS} days so you can undo it" in page      # the undo / purge period
    assert guest_memory.ttl == 1800 and "about 30 minutes" in page     # how long a guest's chat stays in memory


def test_privacy_page_does_not_overpromise(web):
    page = text_of(web.get("/privacy").text).lower()
    for claim in ("we never share", "100% secure", "fully compliant", "gdpr", "military", "unhackable", "we never keep anything"):
        assert claim not in page, claim


def test_privacy_contact_email_is_optional_and_safe(web, monkeypatch):
    assert 'href="/about"' in web.get("/privacy").text and "mailto:" not in web.get("/privacy").text
    monkeypatch.setenv("PRIVACY_CONTACT_EMAIL", "privacy@example.com")
    assert 'href="mailto:privacy@example.com"' in web.get("/privacy").text
    for bad in ('x"><script>alert(1)</script>@e.com', "not an address", "a@b", "javascript:alert(1)@x.com", "<b>@x.com"):
        monkeypatch.setenv("PRIVACY_CONTACT_EMAIL", bad)
        html = web.get("/privacy").text
        assert "mailto:" not in html and "<script>alert" not in html, bad


def test_privacy_page_links_to_settings_for_export_and_delete(web):
    assert 'href="/settings"' in web.get("/privacy").text


# ── the settings page ────────────────────────────────────────────────────────────

def test_settings_page_has_export_delete_and_privacy_controls(web):
    r = web.get("/settings")
    assert r.status_code == 200
    for hook in ('data-testid="download-json"', 'data-testid="download-csv"', 'data-testid="delete-password"',
                 'data-testid="delete-understand"', 'data-testid="delete-submit"', 'href="/privacy"',
                 '/static/js/settings.js', 'type="password"', 'autocomplete="current-password"'):
        assert hook in r.text, hook


def test_the_settings_script_is_served_and_the_page_is_not_cached_by_the_service_worker(web):
    assert web.get("/static/js/settings.js").status_code == 200
    sw = (STATIC / "sw.js").read_text(encoding="utf-8")
    assert "'/auth/'" in sw and "'/api/'" in sw                       # export and delete are never cached


# ── links ────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/about", "/expenses", "/privacy", "/settings", "/zakat"])
def test_every_page_links_to_settings_and_privacy(web, path):
    html = web.get(path).text
    assert 'href="/settings"' in html and 'href="/privacy"' in html


# ── settings.js ──────────────────────────────────────────────────────────────────

def _dictionaries():
    js = (STATIC / "js/settings.js").read_text(encoding="utf-8")
    en = js[js.index("    en: {"):js.index("    ur: {")]
    ur = js[js.index("    ur: {"):js.index("  const WRITE_HEADER")]
    keys = lambda block: dict(re.findall(r"\b([a-z_0-9]+): '((?:[^'\\]|\\.)*)'", block))
    return js, keys(en), keys(ur)


def test_settings_urdu_and_english_have_the_same_keys_and_placeholders():
    _, en, ur = _dictionaries()
    assert len(en) >= 25 and set(en) == set(ur)
    for k in en:
        assert re.findall(r"\{\w+\}", en[k]) == re.findall(r"\{\w+\}", ur[k]), k
        assert en[k].strip() and ur[k].strip()


def test_settings_urdu_is_urdu_and_marked_for_review():
    js, en, ur = _dictionaries()
    assert "awaits native-speaker review" in js and "UR:" in js
    arabic = re.compile(r"[؀-ۿ]")
    for k, v in ur.items():
        assert arabic.search(v), (k, v)


def test_every_text_key_used_by_the_settings_page_exists():
    js, en, _ = _dictionaries()
    html = (ROOT / "frontend/templates/pages/settings.html").read_text(encoding="utf-8")
    used = set(re.findall(r"\bt\('([a-z_0-9]+)'[,)]", html + js))
    assert used and not used - set(en), used - set(en)
    unused = set(en) - used
    assert not unused, f"dictionary keys nobody uses: {unused}"


def test_the_settings_script_talks_to_the_right_endpoints_safely():
    js = (STATIC / "js/settings.js").read_text(encoding="utf-8")
    assert "user_id" not in js and "console.log" not in js
    assert "X-Requested-With" in js and "ShariahEase" in js
    assert "/api/expenses/export?format=" in js and "'/auth/me'" in js and "method: 'DELETE'" in js
    assert "credentials: 'same-origin'" in js
    assert "JSON.stringify({ password: this.password })" in js                              # the password really is sent
    assert js.count("window.location.assign('/')") == 1
    assert "store.remove('se_token')" in js and "store.remove('se_user')" in js         # the login is dropped after deleting


def test_settings_is_linked_from_both_the_sidebar_and_the_profile_menu(web):
    html = web.get("/about").text
    assert '<span class="font-body-md text-body-md">Settings</span>' in html              # sidebar
    assert 'text-[18px]">settings</span>' in html                                           # profile menu
    assert html.count('href="/settings"') == 2
    assert '<span class="font-body-md text-body-md">Privacy</span>' in html
