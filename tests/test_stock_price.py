import asyncio
import logging
from datetime import timezone

import httpx
import pytest

from app.services import stock_price as sp

EPOCH = 1_761_250_000          # 2025-10-23 ~20:46 UTC
SECRET = "sk-test-SECRET-key-123"


def run(coro):
    return asyncio.run(coro)


# ── sample payloads in the formats the providers are documented/known to use ───

def yahoo_ok(price=231.45, prev=230.25, currency="USD", name="Apple Inc."):
    return {"chart": {"result": [{"meta": {
        "regularMarketPrice": price, "chartPreviousClose": prev, "currency": currency,
        "regularMarketTime": EPOCH, "longName": name,
    }}], "error": None}}


def finnhub_ok(price=231.45):
    return {"c": price, "d": 1.2, "dp": 0.52, "h": 0, "l": 0, "o": 0, "pc": 230.25, "t": EPOCH}


def psx_ok():
    # deliberately not in time order, and the newest row uses milliseconds
    return {"status": 1, "message": "", "data": [
        [EPOCH - 600, 410.0, 1000], [EPOCH * 1000, 412.55, 500], [EPOCH - 60, 411.0, 200],
    ]}


@pytest.fixture
def net(monkeypatch):
    """
    Route price requests to handlers by host. Usage:
        net.on("finnhub.io", lambda req: httpx.Response(200, json=...))
    Unhandled hosts fail as if offline. All requests are recorded in net.requests.
    """
    class Net:
        def __init__(self):
            self.handlers = {}
            self.requests = []

        def on(self, host, handler):
            self.handlers[host] = handler

        def hits(self, host):
            return [r for r in self.requests if r.url.host == host]

    state = Net()

    def handler(request):
        state.requests.append(request)
        h = state.handlers.get(request.url.host)
        if h is None:
            raise httpx.ConnectError("offline", request=request)
        return h(request)

    monkeypatch.setattr(
        sp, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    return state


def json_response(payload, status=200):
    return lambda request: httpx.Response(status, json=payload)


# ── parsers ────────────────────────────────────────────────────────────────────

def test_parse_yahoo_chart():
    q = sp.parse_yahoo_chart(yahoo_ok(), "AAPL", sp.GLOBAL)
    assert (q.symbol, q.price, q.currency, q.source, q.name) == (
        "AAPL", 231.45, "USD", "Yahoo Finance", "Apple Inc.")
    assert q.change == pytest.approx(1.20)
    assert q.change_pct == pytest.approx(1.20 / 230.25 * 100)
    assert q.as_of.tzinfo == timezone.utc


@pytest.mark.parametrize("payload", [
    None, {}, {"chart": {"result": None}}, {"chart": {"result": []}},
    {"chart": {"result": [{"meta": {}}]}},
    {"chart": {"result": [{"meta": {"regularMarketPrice": 0}}]}},
    {"chart": {"result": [{"meta": {"regularMarketPrice": "n/a"}}]}},
])
def test_parse_yahoo_chart_rejects_unusable_payloads(payload):
    assert sp.parse_yahoo_chart(payload, "AAPL", sp.GLOBAL) is None


def test_parse_finnhub_quote():
    q = sp.parse_finnhub_quote(finnhub_ok(), "AAPL")
    assert (q.price, q.currency, q.source) == (231.45, "USD", "Finnhub")
    assert q.change_pct == pytest.approx(0.52)


def test_finnhub_all_zeros_means_unknown_symbol():
    assert sp.parse_finnhub_quote({"c": 0, "d": None, "dp": None, "t": 0}, "ZZZZ") is None


def test_parse_psx_picks_latest_row_whatever_the_order_and_handles_milliseconds():
    q = sp.parse_psx_timeseries(psx_ok(), "LUCK")
    assert (q.price, q.currency, q.market) == (412.55, "PKR", sp.PSX)
    assert int(q.as_of.timestamp()) == EPOCH


@pytest.mark.parametrize("payload", [
    None, [], {}, {"data": None}, {"data": []}, {"data": ["x", [1]]},
    {"data": [[EPOCH, 0, 1]]}, {"data": [[EPOCH, "abc", 1]]},
])
def test_parse_psx_rejects_unusable_payloads(payload):
    assert sp.parse_psx_timeseries(payload, "LUCK") is None


# ── provider chain ─────────────────────────────────────────────────────────────

def test_psx_symbol_uses_the_psx_portal_first(net):
    net.on("dps.psx.com.pk", json_response(psx_ok()))
    q = run(sp.get_quote("LUCK", sp.PSX))
    assert (q.price, q.source) == (412.55, "PSX data portal")
    assert net.hits("query1.finance.yahoo.com") == []


def test_psx_falls_back_to_yahoo_with_the_ka_suffix(net):
    net.on("dps.psx.com.pk", json_response({}, status=503))
    net.on("query1.finance.yahoo.com", json_response(yahoo_ok(410.1, 408.0, "PKR", "Lucky Cement")))
    q = run(sp.get_quote("LUCK", sp.PSX))
    assert (q.symbol, q.price, q.currency, q.market) == ("LUCK", 410.1, "PKR", sp.PSX)
    assert net.hits("query1.finance.yahoo.com")[0].url.path.endswith("/LUCK.KA")


def test_psx_portal_format_change_also_falls_back(net):
    net.on("dps.psx.com.pk", json_response({"unexpected": "shape"}))
    net.on("query1.finance.yahoo.com", json_response(yahoo_ok(410.1, 408.0, "PKR")))
    assert run(sp.get_quote("LUCK", sp.PSX)).source == "Yahoo Finance"


def test_global_symbol_without_a_key_goes_straight_to_yahoo(net):
    net.on("query1.finance.yahoo.com", json_response(yahoo_ok()))
    q = run(sp.get_quote("AAPL", sp.GLOBAL))
    assert q.source == "Yahoo Finance"
    assert net.hits("finnhub.io") == []


def test_global_symbol_with_a_key_uses_finnhub_and_keeps_the_key_out_of_the_url(net, monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", SECRET)
    net.on("finnhub.io", json_response(finnhub_ok()))
    q = run(sp.get_quote("AAPL", sp.GLOBAL))
    assert q.source == "Finnhub"
    request = net.hits("finnhub.io")[0]
    assert request.headers["X-Finnhub-Token"] == SECRET
    assert SECRET not in str(request.url)
    assert net.hits("query1.finance.yahoo.com") == []


def test_finnhub_zeros_fall_back_to_yahoo(net, monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", SECRET)
    net.on("finnhub.io", json_response({"c": 0, "t": 0}))
    net.on("query1.finance.yahoo.com", json_response(yahoo_ok()))
    assert run(sp.get_quote("SAP", sp.GLOBAL)).source == "Yahoo Finance"


def test_finnhub_is_skipped_for_symbols_with_an_exchange_suffix(net, monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", SECRET)
    net.on("query1.finance.yahoo.com", json_response(yahoo_ok(2500, 2490, "JPY", "Toyota")))
    run(sp.get_quote("7203.T", sp.GLOBAL))
    assert net.hits("finnhub.io") == []


def test_api_key_never_appears_in_logs_even_when_the_provider_errors(net, monkeypatch, caplog):
    monkeypatch.setenv("FINNHUB_API_KEY", SECRET)
    net.on("finnhub.io", json_response({"error": "Invalid API key"}, status=401))
    net.on("query1.finance.yahoo.com", json_response({}, status=429))
    with caplog.at_level(logging.DEBUG):
        assert run(sp.get_quote("AAPL", sp.GLOBAL)) is None
    assert "HTTP 401" in caplog.text and "HTTP 429" in caplog.text
    assert SECRET not in caplog.text


def test_every_provider_failing_returns_none_not_an_exception(net):
    assert run(sp.get_quote("AAPL", sp.GLOBAL)) is None      # all hosts offline
    assert run(sp.get_quote("LUCK", sp.PSX)) is None


def test_invalid_symbols_never_reach_the_network(net):
    for bad in ["", "../etc", "AAPL; DROP", "A" * 30, "a b"]:
        assert run(sp.get_quote(bad, sp.GLOBAL)) is None
    assert net.requests == []


# ── cache ──────────────────────────────────────────────────────────────────────

def test_a_good_quote_is_cached(net):
    net.on("query1.finance.yahoo.com", json_response(yahoo_ok()))
    run(sp.get_quote("AAPL", sp.GLOBAL))
    run(sp.get_quote("aapl", sp.GLOBAL))       # same symbol, different case
    assert len(net.hits("query1.finance.yahoo.com")) == 1


def test_failure_is_remembered_briefly_so_a_blocked_api_is_not_hammered(net):
    net.on("query1.finance.yahoo.com", json_response({}, status=429))
    run(sp.get_quote("AAPL", sp.GLOBAL))
    run(sp.get_quote("AAPL", sp.GLOBAL))
    assert len(net.hits("query1.finance.yahoo.com")) == 1


def test_expired_cache_entries_are_refetched(net, monkeypatch):
    net.on("query1.finance.yahoo.com", json_response(yahoo_ok()))
    run(sp.get_quote("AAPL", sp.GLOBAL))
    key = (sp.GLOBAL, "AAPL")
    expires, quote = sp._cache[key]
    sp._cache[key] = (expires - sp.QUOTE_TTL - 1, quote)
    run(sp.get_quote("AAPL", sp.GLOBAL))
    assert len(net.hits("query1.finance.yahoo.com")) == 2


# ── symbol resolution ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, expected", [
    ("Is Apple stock halal?", [("AAPL", sp.GLOBAL)]),
    ("what is tesla trading at", [("TSLA", sp.GLOBAL)]),
    ("Lucky Cement share price", [("LUCK", sp.PSX)]),
    ("OGDC", [("OGDC", sp.PSX)]),
    ("$AAPL", [("AAPL", sp.GLOBAL)]),
    ("$LUCK today", [("LUCK", sp.PSX)]),
    ("Meezan Bank vs Tesla", [("MEBL", sp.PSX), ("TSLA", sp.GLOBAL)]),
    ("apple and apple again", [("AAPL", sp.GLOBAL)]),
])
def test_resolve_symbols(text, expected):
    assert sp.resolve_symbols(text) == expected


@pytest.mark.parametrize("text", [
    "pineapple juice", "reliable energy stocks", "What is riba?", "Bitcoin", "hello",
    "Meezan Islamic Fund",
])
def test_resolve_symbols_ignores_text_that_names_no_stock(text):
    assert [s for s, m in sp.resolve_symbols(text) if m == sp.GLOBAL] == []


def test_resolve_symbols_is_capped():
    text = "apple tesla microsoft amazon nvidia netflix"
    assert len(sp.resolve_symbols(text)) == sp.MAX_SYMBOLS


def test_mentions_global_company():
    assert sp.mentions_global_company("Is Apple halal?")
    assert sp.mentions_global_company("buy $NVDA")
    assert not sp.mentions_global_company("pineapple")
    assert not sp.mentions_global_company("What is zakat?")


# ── name search (screener fallback) ────────────────────────────────────────────

def search_payload(*quotes):
    return {"quotes": [dict(q) for q in quotes]}


def test_search_accepts_an_equity_whose_name_contains_the_query(net):
    net.on("query2.finance.yahoo.com", json_response(search_payload(
        {"symbol": "NKE", "quoteType": "EQUITY", "shortname": "NIKE, Inc."})))
    assert run(sp.search_equity("Nike")) == ("NKE", sp.GLOBAL)


def test_search_maps_karachi_symbols_to_psx(net):
    net.on("query2.finance.yahoo.com", json_response(search_payload(
        {"symbol": "HUBC.KA", "quoteType": "EQUITY", "shortname": "HUB POWER CO LTD"})))
    assert run(sp.search_equity("Hub Power")) == ("HUBC", sp.PSX)


def test_search_rejects_crypto_and_unrelated_names(net):
    net.on("query2.finance.yahoo.com", json_response(search_payload(
        {"symbol": "BTC-USD", "quoteType": "CRYPTOCURRENCY", "shortname": "Bitcoin USD"},
        {"symbol": "XYZ", "quoteType": "EQUITY", "shortname": "Something Else Corp"})))
    assert run(sp.search_equity("Bitcoin")) is None


def test_get_quotes_for_query_uses_search_only_for_unknown_company_names(net):
    net.on("query2.finance.yahoo.com", json_response(search_payload(
        {"symbol": "NKE", "quoteType": "EQUITY", "shortname": "NIKE, Inc."})))
    net.on("query1.finance.yahoo.com", json_response(yahoo_ok(80.0, 79.0, "USD", "NIKE, Inc.")))
    quotes = run(sp.get_quotes_for_query("Nike"))
    assert [q.symbol for q in quotes] == ["NKE"]


def test_get_quotes_for_query_does_not_search_for_funds(net):
    run(sp.get_quotes_for_query("HBL Islamic Money Market Fund"))
    assert net.hits("query2.finance.yahoo.com") == []


def test_get_quotes_for_query_does_not_search_when_the_name_is_already_known(net):
    net.on("query1.finance.yahoo.com", json_response(yahoo_ok()))
    run(sp.get_quotes_for_query("Apple"))
    assert net.hits("query2.finance.yahoo.com") == []


# ── formatting ─────────────────────────────────────────────────────────────────

def test_context_block_states_price_currency_time_and_source():
    q = sp.parse_yahoo_chart(yahoo_ok(), "AAPL", sp.GLOBAL)
    text = sp.format_quotes_context([q])
    assert "Apple Inc. (AAPL): USD 231.45" in text
    assert "+1.20 (+0.52%)" in text
    assert "UTC" in text and "source: Yahoo Finance" in text
    assert "never state or estimate a price for any other stock" in text


def test_context_block_is_empty_without_quotes():
    assert sp.format_quotes_context([]) == ""


def test_price_line_without_change_or_time():
    q = sp.Quote("LUCK", sp.PSX, 1234.5, "PKR", "PSX data portal")
    line = sp.format_price_line(q)
    assert "LUCK PKR 1,234.50" in line and "time not available" in line
    assert "may be delayed" in line
