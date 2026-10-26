import asyncio
import logging
import pathlib
import time
from datetime import timezone

import httpx
import pytest

from app.services import stock_price as sp

EPOCH = int(time.time()) - 3600          # one hour ago: always "fresh"
OLD_EPOCH = 1_721_764_800                # 23 Jul 2024, what Yahoo served for LUCK.KA
SECRET = "sk-test-SECRET-key-123"
PSX_TOKEN = "tok_ABCDEFGHIJKLMNOP1234567890"

# Five real rows (OGDC, MEBL, LUCK, DWTM, EPCLPS) from dps.psx.com.pk/trading-board/REG/main
PSX_BOARD_HTML = (
    pathlib.Path(__file__).parent / "fixtures" / "psx_trading_board_sample.html"
).read_text(encoding="utf-8")
PSX_HOME_HTML = (
    '<html><script>window.__ps = {"lc":"en","tz":"Asia/Karachi","_k":"%s","rv":"1"};'
    "</script></html>" % PSX_TOKEN
)


def run(coro):
    return asyncio.run(coro)


# ── sample payloads in the formats the providers are documented/known to use ───

def yahoo_ok(price=231.45, prev=230.25, currency="USD", name="Apple Inc.", when=None):
    return {"chart": {"result": [{"meta": {
        "regularMarketPrice": price, "chartPreviousClose": prev, "currency": currency,
        "regularMarketTime": when or EPOCH, "longName": name,
    }}], "error": None}}


def finnhub_ok(price=231.45):
    return {"c": price, "d": 1.2, "dp": 0.52, "h": 0, "l": 0, "o": 0, "pc": 230.25, "t": EPOCH}


def psx_site(token_required=True):
    """Handlers for dps.psx.com.pk that behave like the real site: the homepage
    hands out a token and data requests without it get a 403."""
    def handler(request):
        if request.url.path == "/":
            return httpx.Response(200, text=PSX_HOME_HTML)
        if token_required and request.headers.get("X-Req-Id") != PSX_TOKEN:
            return httpx.Response(403, text="forbidden")
        return httpx.Response(200, text=PSX_BOARD_HTML)
    return handler


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


def test_parse_psx_trading_board_real_rows():
    board = sp.parse_psx_trading_board(PSX_BOARD_HTML)
    assert set(board) == {"OGDC", "MEBL", "LUCK", "DWTM", "EPCLPS"}

    luck = board["LUCK"]                       # LDCP 382.12 + CHANGE 38.21
    assert luck.price == pytest.approx(420.33)
    assert (luck.currency, luck.market, luck.source) == ("PKR", sp.PSX, "PSX trading board")
    assert luck.name == "Lucky Cement Limited"
    assert luck.change == pytest.approx(38.21)
    assert luck.change_pct == pytest.approx(38.21 / 382.12 * 100)
    assert luck.as_of is None                  # the board carries no timestamp

    assert board["MEBL"].price == pytest.approx(481.16 + 32.33)
    assert board["OGDC"].name == "Oil & Gas Development Company Limited"   # entities decoded


def test_parse_psx_trading_board_negative_and_zero_change():
    board = sp.parse_psx_trading_board(PSX_BOARD_HTML)
    assert board["DWTM"].price == pytest.approx(8.47 - 0.07)
    assert board["DWTM"].change < 0
    assert board["EPCLPS"].price == pytest.approx(12.25)
    assert board["EPCLPS"].change == 0


def test_parse_psx_uses_a_current_column_if_psx_ever_adds_one():
    html = ("<table><tr><th>SYMBOL</th><th>LDCP</th><th>CURRENT</th><th>CHANGE</th></tr>"
            '<tr><td data-order="ABCD">ABCD</td><td data-order="10">10</td>'
            '<td data-order="11.5">11.50</td><td data-order="1.5">1.50</td></tr></table>')
    assert sp.parse_psx_trading_board(html)["ABCD"].price == 11.5


def test_parse_psx_finds_columns_by_name_not_position():
    html = ("<table><tr><th>NAME</th><th>CHANGE</th><th>SYMBOL</th><th>LDCP</th></tr>"
            '<tr><td>X Ltd</td><td data-order="-2">-2</td><td data-order="XYZ">XYZ</td>'
            '<td data-order="50">50</td></tr></table>')
    assert sp.parse_psx_trading_board(html)["XYZ"].price == 48.0


@pytest.mark.parametrize("html", [
    None, "", "<html></html>", "<table><tr><th>FOO</th></tr><tr><td>1</td></tr></table>",
    "<table><tr><th>SYMBOL</th><th>LDCP</th><th>CHANGE</th></tr>"
    '<tr><td data-order="BAD">BAD</td><td data-order="n/a">n/a</td><td data-order="x">x</td></tr></table>',
    "<table><tr><th>SYMBOL</th><th>LDCP</th><th>CHANGE</th></tr>"
    '<tr><td data-order="ZERO">ZERO</td><td data-order="1">1</td><td data-order="-1">-1</td></tr></table>',
])
def test_parse_psx_trading_board_rejects_unusable_pages(html):
    assert sp.parse_psx_trading_board(html) == {}


def test_extract_psx_token():
    assert sp.extract_psx_token(PSX_HOME_HTML) == PSX_TOKEN
    assert sp.extract_psx_token("<html>no script</html>") is None
    assert sp.extract_psx_token('window.__ps = {"_k":"short"};') is None
    assert sp.extract_psx_token("window.__ps = {not json};") is None
    assert sp.extract_psx_token(None) is None


# ── provider chain ─────────────────────────────────────────────────────────────

def test_psx_symbol_uses_the_trading_board_with_the_token(net):
    net.on("dps.psx.com.pk", psx_site())
    q = run(sp.get_quote("LUCK", sp.PSX))
    assert (q.price, q.source) == (pytest.approx(420.33), "PSX trading board")
    board_request = [r for r in net.hits("dps.psx.com.pk") if r.url.path != "/"][0]
    assert board_request.url.path == "/trading-board/REG/main"
    assert board_request.headers["X-Req-Id"] == PSX_TOKEN
    assert net.hits("query1.finance.yahoo.com") == []


def test_one_board_download_and_one_token_serve_every_psx_symbol(net):
    net.on("dps.psx.com.pk", psx_site())
    for symbol in ("LUCK", "OGDC", "MEBL"):
        assert run(sp.get_quote(symbol, sp.PSX)) is not None
    paths = [r.url.path for r in net.hits("dps.psx.com.pk")]
    assert paths.count("/") == 1 and paths.count("/trading-board/REG/main") == 1


def test_a_403_gets_a_fresh_token_and_one_retry(net):
    net.on("dps.psx.com.pk", psx_site())
    sp._psx_token = ("expired-token-AAAAAAAAAAAAAAAA", time.monotonic())   # looks fresh, PSX rejects it
    q = run(sp.get_quote("LUCK", sp.PSX))
    assert q.source == "PSX trading board"
    assert [r.url.path for r in net.hits("dps.psx.com.pk")] == [
        "/trading-board/REG/main", "/", "/trading-board/REG/main"]


def test_psx_data_is_requested_without_a_header_when_no_token_can_be_found(net):
    # fail open, like the psxdata project: if PSX drops the mechanism it still works
    def handler(request):
        if request.url.path == "/":
            return httpx.Response(200, text="<html>no token here</html>")
        assert "X-Req-Id" not in request.headers
        return httpx.Response(200, text=PSX_BOARD_HTML)
    net.on("dps.psx.com.pk", handler)
    assert run(sp.get_quote("LUCK", sp.PSX)).source == "PSX trading board"


def test_a_symbol_missing_from_the_board_falls_back(net):
    net.on("dps.psx.com.pk", psx_site())
    net.on("query1.finance.yahoo.com", json_response(yahoo_ok(10.0, 9.0, "PKR", "Some Co")))
    assert run(sp.get_quote("ZZZZ", sp.PSX)).source == "Yahoo Finance"


def test_psx_falls_back_to_yahoo_with_the_ka_suffix_when_the_board_is_down(net):
    net.on("dps.psx.com.pk", lambda request: httpx.Response(503))
    net.on("query1.finance.yahoo.com", json_response(yahoo_ok(410.1, 408.0, "PKR", "Lucky Cement")))
    q = run(sp.get_quote("LUCK", sp.PSX))
    assert (q.symbol, q.price, q.currency, q.market) == ("LUCK", 410.1, "PKR", sp.PSX)
    assert net.hits("query1.finance.yahoo.com")[0].url.path.endswith("/LUCK.KA")


def test_psx_page_format_change_also_falls_back(net):
    net.on("dps.psx.com.pk", lambda request: httpx.Response(
        200, text=PSX_HOME_HTML if request.url.path == "/" else "<html>redesigned</html>"))
    net.on("query1.finance.yahoo.com", json_response(yahoo_ok(410.1, 408.0, "PKR")))
    assert run(sp.get_quote("LUCK", sp.PSX)).source == "Yahoo Finance"


def test_the_psx_token_never_appears_in_logs(net, caplog):
    net.on("dps.psx.com.pk", lambda request: (
        httpx.Response(200, text=PSX_HOME_HTML) if request.url.path == "/"
        else httpx.Response(403)))
    with caplog.at_level(logging.DEBUG):
        run(sp.get_quote("LUCK", sp.PSX))
    assert "HTTP 403" in caplog.text
    assert PSX_TOKEN not in caplog.text


# ── stale prices are never presented as current ────────────────────────────────

def test_yahoos_fifteen_month_old_price_for_a_psx_stock_is_ignored(net):
    """What happened in the live chat: PSX failed, Yahoo's .KA data was from July 2024."""
    net.on("dps.psx.com.pk", lambda request: httpx.Response(403))
    net.on("query1.finance.yahoo.com", json_response(
        yahoo_ok(907.65, 909.67, "PKR", "Lucky Cement Limited", when=OLD_EPOCH)))
    assert run(sp.get_quote("LUCK", sp.PSX)) is None


def test_a_recent_price_just_inside_the_limit_is_accepted(net):
    nine_days_ago = int(time.time()) - 9 * 86400
    net.on("query1.finance.yahoo.com", json_response(yahoo_ok(when=nine_days_ago)))
    assert run(sp.get_quote("AAPL", sp.GLOBAL)) is not None


def test_a_price_just_outside_the_limit_is_rejected(net):
    eleven_days_ago = int(time.time()) - 11 * 86400
    net.on("query1.finance.yahoo.com", json_response(yahoo_ok(when=eleven_days_ago)))
    assert run(sp.get_quote("AAPL", sp.GLOBAL)) is None


def test_a_quote_with_no_timestamp_is_not_treated_as_stale():
    assert sp._is_stale(sp.Quote("X", sp.PSX, 1.0, "PKR", "PSX trading board")) is False


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
    assert "LUCK PKR 1,234.50" in line and "time not provided by the source" in line
    assert "may be delayed" in line


def test_a_quote_that_is_days_old_but_accepted_is_labelled_as_the_last_available_price():
    from datetime import datetime, timedelta, timezone
    q = sp.Quote("AAPL", sp.GLOBAL, 100.0, "USD", "Yahoo Finance",
                 as_of=datetime.now(timezone.utc) - timedelta(days=4))
    assert "last available price" in sp.format_price_line(q)
    assert "last available price" in sp.format_quotes_context([q])
