"""Step 1 fix batch: no key in the source, honest failure without it, current year in queries,
and every package the code imports lazily is listed in requirements.txt."""

import asyncio
import logging
import re
from pathlib import Path

import pytest

from app.services import web_search

ROOT = Path(__file__).resolve().parent.parent


def run(coro):
    return asyncio.run(coro)


# ── no key in the source ────────────────────────────────────────────────────────

def test_no_api_key_is_hard_coded_in_the_app_code():
    pattern = re.compile(r"""x-api-key["']?\s*[:=]\s*["'][A-Za-z0-9_\-]{12,}["']""", re.I)
    offenders = [str(p.relative_to(ROOT)) for p in (ROOT / "app").rglob("*.py")
                 if pattern.search(p.read_text(encoding="utf-8"))]
    assert offenders == []


# ── SUNNAH_API_KEY ──────────────────────────────────────────────────────────────

class _NoNetwork:
    """Stands in for httpx.AsyncClient; any use is a test failure."""
    def __init__(self, *a, **k):
        raise AssertionError("a request was made without SUNNAH_API_KEY")


@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.delenv("SUNNAH_API_KEY", raising=False)
    monkeypatch.setattr(web_search, "_sunnah_key_warned", False)
    monkeypatch.setattr(web_search.httpx, "AsyncClient", _NoNetwork)


def test_without_the_key_no_request_is_made_and_the_answer_says_so(no_key):
    out = run(web_search._search_sunnah_com("zakat"))
    assert "HADITH LOOKUP UNAVAILABLE" in out
    assert "not configured" in out


def test_a_blank_key_counts_as_unset(no_key, monkeypatch):
    monkeypatch.setenv("SUNNAH_API_KEY", "   ")
    assert "HADITH LOOKUP UNAVAILABLE" in run(web_search._search_sunnah_com("zakat"))


def test_the_missing_key_is_logged_once_and_never_logs_a_value(no_key, caplog):
    with caplog.at_level(logging.WARNING, logger=web_search.logger.name):
        run(web_search._search_sunnah_com("zakat"))
        run(web_search._search_sunnah_com("riba"))
    warnings = [r.getMessage() for r in caplog.records if "SUNNAH_API_KEY" in r.getMessage()]
    assert len(warnings) == 1


def test_the_key_from_the_environment_is_what_is_sent(monkeypatch):
    monkeypatch.setenv("SUNNAH_API_KEY", "test-key-123")
    seen = {}

    class FakeResponse:
        status_code = 200
        def json(self):
            return {"data": []}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, params=None, headers=None):
            seen["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr(web_search.httpx, "AsyncClient", FakeClient)
    assert run(web_search._search_sunnah_com("zakat")) == ""
    assert seen["headers"] == {"X-API-Key": "test-key-123"}


def test_a_hadith_question_still_gets_an_answer_context_without_the_key(no_key, monkeypatch):
    async def no_web(query, max_results=4):
        return []
    monkeypatch.setattr(web_search, "web_search", no_web)
    context, did_search = run(web_search.get_web_context("give me a hadith about zakat"))
    assert did_search is True
    assert "HADITH LOOKUP UNAVAILABLE" in context


# ── the year is not hard-coded ──────────────────────────────────────────────────

@pytest.fixture
def year_2031(monkeypatch):
    monkeypatch.setattr(web_search, "_current_year", lambda: 2031)


def test_price_query_uses_the_current_year(year_2031):
    assert web_search._build_price_query("lucky cement price", "lucky cement").endswith("2031")
    assert "2031" in web_search._build_price_query("tesla price", None)


@pytest.mark.parametrize("message, expected_category", [
    ("gold rate today", web_search.SearchCategory.GOLD_SILVER),
    ("SBP policy rate news", web_search.SearchCategory.FINANCE_NEWS),
])
def test_search_queries_use_the_current_year(year_2031, monkeypatch, message, expected_category):
    queries = []

    async def fake_search(query, max_results=4):
        queries.append(query)
        return []

    monkeypatch.setattr(web_search, "web_search", fake_search)
    assert web_search.classify_for_search(message) is expected_category
    run(web_search.get_web_context(message))
    assert queries and all("2031" in q for q in queries)
    assert not any("2025" in q for q in queries)


def test_current_year_is_the_real_year():
    from datetime import datetime, timezone
    assert web_search._current_year() == datetime.now(timezone.utc).year


# ── requirements.txt ────────────────────────────────────────────────────────────

def test_edge_tts_is_listed_and_pinned():
    lines = [l.split("#")[0].strip() for l in (ROOT / "requirements.txt").read_text().splitlines()]
    assert any(re.fullmatch(r"edge-tts==\d+\.\d+\.\d+", l) for l in lines)
