import asyncio
from types import SimpleNamespace

import pytest

from app.models.schemas import ScreenerRequest
from app.routers import screener as screener_router
from app.services import screener_graph as sg
from app.services import stock_price as sp
from app.services import web_search as ws

APPLE = sp.Quote("AAPL", sp.GLOBAL, 231.45, "USD", "Yahoo Finance",
                 name="Apple Inc.", change=1.2, change_pct=0.52)
LUCK = sp.Quote("LUCK", sp.PSX, 412.55, "PKR", "PSX data portal")


def run(coro):
    return asyncio.run(coro)


# ── chatbot context (get_web_context) ──────────────────────────────────────────

@pytest.fixture
def web(monkeypatch):
    log = {"ddg": [], "quote_calls": [], "quotes": []}

    async def fake_quotes(text):
        log["quote_calls"].append(text)
        return log["quotes"]

    async def fake_ddg(query, max_results=4):
        log["ddg"].append(query)
        return [{"title": "PSX page", "url": "", "snippet": "DDG price snippet"}]

    monkeypatch.setattr(ws, "get_quotes_for_text", fake_quotes)
    monkeypatch.setattr(ws, "web_search", fake_ddg)
    return log


def test_stock_question_uses_the_price_api_and_skips_the_web_scrape(web):
    web["quotes"] = [APPLE]
    context, did_search = run(ws.get_web_context("Is Apple stock halal?"))
    assert "Apple Inc. (AAPL): USD 231.45" in context
    assert "DDG price snippet" not in context
    assert web["ddg"] == []
    assert did_search is True


def test_when_no_price_api_answers_it_falls_back_to_the_web_search(web):
    web["quotes"] = []
    context, _ = run(ws.get_web_context("Is Apple stock halal?"))
    assert "LIVE STOCK QUOTES" not in context
    assert "DDG price snippet" in context
    assert len(web["ddg"]) == 1


def test_include_quotes_false_keeps_the_old_behaviour(web):
    web["quotes"] = [APPLE]
    context, _ = run(ws.get_web_context("Is Apple stock halal?", include_quotes=False))
    assert web["quote_calls"] == []
    assert "DDG price snippet" in context


def test_psx_company_gets_both_the_shariah_block_and_the_quote(web):
    web["quotes"] = [LUCK]
    context, _ = run(ws.get_web_context("Lucky Cement share price"))
    assert "SHARIAHEASE SHARIAH COMPLIANCE DATABASE" in context
    assert "LUCK: PKR 412.55" in context
    assert web["ddg"] == []


def test_non_stock_questions_trigger_no_price_lookup_at_all(web):
    run(ws.get_web_context("What is the ruling on riba?"))
    assert web["quote_calls"] == [] and web["ddg"] == []


def test_a_foreign_company_name_is_enough_to_count_as_a_price_question():
    assert ws.classify_for_search("Is Apple halal?") == ws.SearchCategory.STOCK_PRICE
    assert ws.classify_for_search("what about $TSLA") == ws.SearchCategory.STOCK_PRICE
    assert ws.classify_for_search("pineapple juice") == ws.SearchCategory.NONE


# ── screener graph ─────────────────────────────────────────────────────────────

@pytest.fixture
def graph(monkeypatch):
    log = {"llm": 0, "web_kwargs": [], "quote_queries": [], "quotes": []}

    def fake_llm(query, context, has_kmi_data):
        log["llm"] += 1
        return {"verdict": "DOUBTFUL", "confidence": "LOW",
                "reason": "llm reason", "recommendation": "llm rec"}

    async def fake_quotes(query):
        log["quote_queries"].append(query)
        return log["quotes"]

    monkeypatch.setattr(sg, "_build_rag_context", lambda q: "RAG")
    async def no_web(query):
        return ""
    monkeypatch.setattr(sg, "_get_web_context", no_web)
    monkeypatch.setattr(sg, "_call_llm", fake_llm)
    monkeypatch.setattr(sg, "get_quotes_for_query", fake_quotes)
    monkeypatch.setattr(sg, "_graph", None)
    return log


def screen(query):
    return run(sg.run_screener(query))


def test_price_line_is_attached_and_the_verdict_is_untouched(graph):
    graph["quotes"] = [LUCK]
    out = screen("Lucky Cement")
    assert out["verdict"] == "HALAL"                    # from the KMI entry, not the price
    assert "LUCK PKR 412.55" in out["price_line"]
    assert graph["llm"] == 1                            # classify ran once, not once per branch


def test_unknown_stock_also_gets_a_price_line(graph):
    graph["quotes"] = [APPLE]
    out = screen("Apple")
    assert out["verdict"] == "DOUBTFUL"
    assert "AAPL USD 231.45" in out["price_line"]
    assert graph["llm"] == 1


def test_no_quote_means_an_empty_price_line(graph):
    out = screen("Bitcoin")
    assert out["price_line"] == ""


def test_a_failing_quote_lookup_does_not_break_screening(graph, monkeypatch):
    async def boom(query):
        raise RuntimeError("provider exploded")
    monkeypatch.setattr(sg, "get_quotes_for_query", boom)
    out = screen("Lucky Cement")
    assert out["verdict"] == "HALAL" and out["price_line"] == ""


def test_screener_web_search_does_not_also_fetch_prices(monkeypatch):
    seen = {}

    async def fake_get_web_context(message, include_quotes=True):
        seen["include_quotes"] = include_quotes
        return "", False

    monkeypatch.setattr(ws, "get_web_context", fake_get_web_context)
    run(sg._get_web_context("Apple"))
    assert seen["include_quotes"] is False


# ── screener router: shown to the user, not saved ──────────────────────────────

def test_router_shows_the_price_but_saves_only_the_reason(monkeypatch):
    saved = {}

    async def fake_run(query):
        return {"verdict": "HALAL", "confidence": "HIGH", "reason": "Because KMI.",
                "recommendation": "", "price_line": "Latest price: LUCK PKR 412.55."}

    monkeypatch.setattr(screener_router, "run_screener", fake_run)
    monkeypatch.setattr(screener_router, "save_screener_result",
                        lambda db, **kw: saved.update(kw))

    response = run(screener_router.check_investment(
        ScreenerRequest(query="Lucky Cement"), db=None, current_user=SimpleNamespace(id=7)))

    assert response.explanation == "Because KMI. Latest price: LUCK PKR 412.55."
    assert saved["explanation"] == "Because KMI."


def test_router_without_a_price_line_is_unchanged(monkeypatch):
    async def fake_run(query):
        return {"verdict": "HARAM", "confidence": "HIGH", "reason": "Riba.",
                "recommendation": "", "price_line": ""}

    monkeypatch.setattr(screener_router, "run_screener", fake_run)
    monkeypatch.setattr(screener_router, "save_screener_result", lambda db, **kw: None)

    response = run(screener_router.check_investment(
        ScreenerRequest(query="HBL"), db=None, current_user=SimpleNamespace(id=7)))
    assert response.explanation == "Riba."
