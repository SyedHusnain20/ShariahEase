"""
ShariahEase — live stock prices from free sources.

No single free API covers both PSX and foreign stocks, so each market gets a
chain of sources and the first one that answers wins:

    PSX     1. PSX's own trading board dps.psx.com.pk   (no key; needs the
                                                         X-Req-Id token PSX
                                                         embeds in its homepage)
            2. Yahoo Finance chart     "<SYMBOL>.KA"     (no key; its PSX data
                                                         is often months old,
                                                         so old quotes are dropped)
    GLOBAL  1. Finnhub                 only if FINNHUB_API_KEY is set; free
                                       plan covers US-listed stocks only
            2. Yahoo Finance chart     (no key)

Things to know before relying on this:
  * Yahoo's endpoint is unofficial. It works without a key but is sometimes
    rate-limited (HTTP 429), especially from shared cloud IPs such as
    Hugging Face Spaces. A free Finnhub key makes US quotes much more reliable.
  * The PSX trading board is PSX's public website data, not a documented API.
    Since 24 Sep 2026 PSX refuses data requests that lack the X-Req-Id token
    (details found in the open-source psxdata project, issue #161).
  * A price older than MAX_AGE_DAYS is never used: Yahoo has served a
    15-month-old "current" price for Pakistani stocks.
  * Free sources are for personal / non-commercial use; check each provider's
    terms before using the data in a paid product.
  * Prices can be delayed and the market may be closed, so every quote
    carries its own "as of" time and source and is shown with them.
  * Nothing here ever raises: a failing provider is logged (never with API
    keys) and the next one is tried; if all fail the caller gets no quote.

Check it from a terminal (needs internet):

    python -m app.services.stock_price LUCK AAPL "Tesla" psx:OGDC us:MSFT
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import unescape
from urllib.parse import quote as _urlquote

import httpx

logger = logging.getLogger(__name__)

TIMEOUT      = 6.0    # seconds per provider call
QUOTE_TTL    = 60     # seconds a good quote is reused (protects free-tier limits)
FAIL_TTL     = 30     # seconds before retrying a symbol every provider failed on
MAX_SYMBOLS  = 3      # quotes fetched for one message
MAX_AGE      = timedelta(days=10)   # longer than any PSX holiday closure + weekends

PSX_BASE         = "https://dps.psx.com.pk"
PSX_TOKEN_HEADER = "X-Req-Id"
PSX_TOKEN_TTL    = 240    # PSX rotates the token about every 5 minutes
PSX_TOKEN_RETRY  = 60     # seconds before trying again after a failed token fetch
PSX_BOARD_TTL    = 60     # the whole board is fetched once and shared by all symbols

PSX    = "PSX"
GLOBAL = "GLOBAL"

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")


@dataclass(frozen=True)
class Quote:
    symbol: str
    market: str                       # PSX | GLOBAL
    price: float
    currency: str
    source: str
    as_of: datetime | None = None     # timezone-aware UTC
    change: float | None = None
    change_pct: float | None = None
    name: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# HTTP
# ═══════════════════════════════════════════════════════════════════════════════

def _client() -> httpx.AsyncClient:
    """One short-lived client per call. Tests replace this with a mock transport."""
    return httpx.AsyncClient(timeout=TIMEOUT, headers=_BROWSER_HEADERS, follow_redirects=True)


async def _get_json(url: str, params: dict | None = None, headers: dict | None = None):
    async with _client() as client:
        response = await client.get(url, params=params, headers=headers)
    response.raise_for_status()
    return response.json()


def _describe(exc: Exception) -> str:
    """Short, key-free description of a failure for the logs.

    httpx puts the full request URL in some exception messages, so the message
    itself is never logged — only the exception type and HTTP status.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return exc.__class__.__name__


def _num(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None   # reject NaN


def _utc(epoch) -> datetime | None:
    seconds = _num(epoch)
    if not seconds or seconds <= 0:
        return None
    if seconds > 1e11:           # milliseconds
        seconds /= 1000
    try:
        return datetime.fromtimestamp(seconds, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# PARSERS  (pure functions — unit-tested without a network)
# ═══════════════════════════════════════════════════════════════════════════════

_ROW_RE   = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_HEAD_RE  = re.compile(r"<th[^>]*>(.*?)</th>", re.S | re.I)
_CELL_RE  = re.compile(r"<td([^>]*)>(.*?)</td>", re.S | re.I)
_ORDER_RE = re.compile(r'data-order="([^"]*)"')
_TAG_RE   = re.compile(r"<[^>]+>")


def _cell_text(attrs: str, inner: str) -> str:
    """A cell's raw value: PSX puts the unformatted number in data-order."""
    match = _ORDER_RE.search(attrs)
    if match:
        return unescape(match.group(1)).strip()
    return unescape(_TAG_RE.sub("", inner)).strip()


def parse_psx_trading_board(html) -> dict[str, Quote]:
    """
    dps.psx.com.pk/trading-board/REG/main  ->  an HTML table of every listed stock:

        SYMBOL | NAME | BID VOL. | BID PRICE | OFFER VOL. | OFFER PRICE | LDCP | CHANGE | VOLUME

    The board has no "current price" column. CHANGE is signed and measured from
    LDCP (last day's closing price), so the latest price is LDCP + CHANGE
    (checked against real rows, e.g. LUCK 382.12 + 38.21 = 420.33, the 10% upper
    limit). If PSX ever adds a CURRENT column, that is used instead.
    Columns are found by header name, not position. The table carries no
    timestamp, so as_of is None.
    """
    if not isinstance(html, str):
        return {}

    columns: dict[str, int] = {}
    quotes: dict[str, Quote] = {}
    for row in _ROW_RE.findall(html):
        headers = [unescape(_TAG_RE.sub("", h)).strip().lower() for h in _HEAD_RE.findall(row)]
        if headers:
            columns = {h.rstrip("."): i for i, h in enumerate(headers)}
            continue
        if "symbol" not in columns:
            continue    # a row before any header: not our table

        cells = [_cell_text(a, i) for a, i in _CELL_RE.findall(row)]
        try:
            symbol = cells[columns["symbol"]].upper()
        except IndexError:
            continue
        if not _SYMBOL_RE.match(symbol):
            continue

        price = change = ldcp = None
        if "current" in columns and columns["current"] < len(cells):
            price = _num(cells[columns["current"]].replace(",", ""))
        if "ldcp" in columns and columns["ldcp"] < len(cells):
            ldcp = _num(cells[columns["ldcp"]].replace(",", ""))
        if "change" in columns and columns["change"] < len(cells):
            change = _num(cells[columns["change"]].replace(",", ""))
        if price is None and ldcp is not None and change is not None:
            price = round(ldcp + change, 2)
        if not price or price <= 0:
            continue

        name = ""
        if "name" in columns and columns["name"] < len(cells):
            name = cells[columns["name"]]
        pct = change / ldcp * 100 if (change is not None and ldcp) else None
        quotes[symbol] = Quote(
            symbol=symbol, market=PSX, price=price, currency="PKR",
            source="PSX trading board", as_of=None,
            change=change, change_pct=pct, name=name,
        )
    return quotes


def parse_yahoo_chart(payload, symbol: str, market: str) -> Quote | None:
    """
    query1.finance.yahoo.com/v8/finance/chart/<SYMBOL>?range=1d&interval=1d
        {"chart": {"result": [{"meta": {"regularMarketPrice": ..., ...}}]}}
    """
    try:
        meta = payload["chart"]["result"][0]["meta"]
    except (KeyError, IndexError, TypeError):
        return None
    if not isinstance(meta, dict):
        return None

    price = _num(meta.get("regularMarketPrice"))
    if not price or price <= 0:
        return None

    previous = _num(meta.get("chartPreviousClose")) or _num(meta.get("previousClose"))
    change = pct = None
    if previous and previous > 0:
        change = price - previous
        pct = change / previous * 100

    return Quote(
        symbol=symbol, market=market, price=price,
        currency=meta.get("currency") or ("PKR" if market == PSX else "USD"),
        source="Yahoo Finance", as_of=_utc(meta.get("regularMarketTime")),
        change=change, change_pct=pct,
        name=(meta.get("longName") or meta.get("shortName") or "").strip(),
    )


def parse_finnhub_quote(payload, symbol: str) -> Quote | None:
    """
    finnhub.io/api/v1/quote  ->  {"c": current, "d": change, "dp": change %, "t": epoch, ...}
    An unknown or unsupported symbol comes back as all zeros.
    """
    if not isinstance(payload, dict):
        return None
    price = _num(payload.get("c"))
    stamp = _num(payload.get("t"))
    if not price or price <= 0 or not stamp:
        return None
    return Quote(
        symbol=symbol, market=GLOBAL, price=price, currency="USD",
        source="Finnhub", as_of=_utc(stamp),
        change=_num(payload.get("d")), change_pct=_num(payload.get("dp")),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# PROVIDERS  (each returns a Quote or None, never raises)
# ═══════════════════════════════════════════════════════════════════════════════

# PSX's data requests need an X-Req-Id token that is embedded in its homepage as
#   window.__ps = {"lc":"en","tz":"Asia/Karachi","_k":"<token>","rv":"1"};
_PS_SCRIPT_RE = re.compile(r"window\.__ps\s*=\s*(\{.*?\})\s*(?:;|</script>)", re.S)
_PS_TOKEN_RE  = re.compile(r"^[A-Za-z0-9_-]{16,}$")

_psx_token: tuple[str, float] | None = None     # (token, fetched at monotonic time)
_psx_token_failed_at: float | None = None
_psx_board: tuple[float, dict[str, Quote]] | None = None   # (expires at, quotes)


def extract_psx_token(html) -> str | None:
    """The window.__ps._k token from a PSX HTML page, or None."""
    import json
    match = _PS_SCRIPT_RE.search(html) if isinstance(html, str) else None
    if not match:
        return None
    try:
        payload = json.loads(match.group(1))
    except ValueError:
        return None
    token = payload.get("_k") if isinstance(payload, dict) else None
    return token if isinstance(token, str) and _PS_TOKEN_RE.fullmatch(token) else None


async def _get_psx_token(force: bool = False) -> str | None:
    """A current token, or None (then requests are sent without it, as psxdata does)."""
    global _psx_token, _psx_token_failed_at
    now = time.monotonic()
    if not force and _psx_token and now - _psx_token[1] < PSX_TOKEN_TTL:
        return _psx_token[0]
    if _psx_token_failed_at is not None and now - _psx_token_failed_at < PSX_TOKEN_RETRY:
        return None
    try:
        async with _client() as client:
            response = await client.get(PSX_BASE + "/", headers={"Accept": "text/html,*/*"})
        response.raise_for_status()
        token = extract_psx_token(response.text)
    except Exception as exc:
        logger.warning("PSX token fetch failed: %s", _describe(exc))
        token = None
    if token:
        _psx_token, _psx_token_failed_at = (token, time.monotonic()), None
        return token
    if token is None:
        logger.warning("PSX homepage had no usable X-Req-Id token")
    _psx_token_failed_at = time.monotonic()
    return None


async def _fetch_psx_board() -> dict[str, Quote]:
    """The whole PSX trading board (one request serves every PSX symbol)."""
    global _psx_token
    url = f"{PSX_BASE}/trading-board/REG/main"
    for attempt in (1, 2):
        headers = {"Referer": PSX_BASE + "/", "X-Requested-With": "XMLHttpRequest",
                   "Accept": "text/html,*/*"}
        token = await _get_psx_token(force=(attempt == 2))
        if token:
            headers[PSX_TOKEN_HEADER] = token
        async with _client() as client:
            response = await client.get(url, headers=headers)
        if response.status_code == 403 and attempt == 1:
            _psx_token = None            # rejected: fetch a fresh token and retry once
            continue
        response.raise_for_status()
        return parse_psx_trading_board(response.text)
    return {}


async def _psx_portal(symbol: str) -> Quote | None:
    global _psx_board
    try:
        if _psx_board is None or _psx_board[0] <= time.monotonic():
            board = await _fetch_psx_board()
            if not board:
                logger.warning("PSX trading board: no rows parsed (page format changed?)")
                return None
            _psx_board = (time.monotonic() + PSX_BOARD_TTL, board)
        quote = _psx_board[1].get(symbol)
        if quote is None:
            logger.warning("PSX trading board: %s not found", symbol)
        return quote
    except Exception as exc:
        logger.warning("PSX trading board failed for %s: %s", symbol, _describe(exc))
        return None


async def _yahoo(symbol: str, market: str) -> Quote | None:
    yahoo_symbol = f"{symbol}.KA" if market == PSX else symbol
    try:
        payload = await _get_json(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{_urlquote(yahoo_symbol)}",
            params={"range": "1d", "interval": "1d"},
        )
        quote = parse_yahoo_chart(payload, symbol, market)
        if quote is None:
            logger.warning("Yahoo: no usable price for %s", yahoo_symbol)
        return quote
    except Exception as exc:
        logger.warning("Yahoo failed for %s: %s", yahoo_symbol, _describe(exc))
        return None


async def _finnhub(symbol: str) -> Quote | None:
    key = os.getenv("FINNHUB_API_KEY", "").strip()
    if not key or "." in symbol:     # free plan: US symbols only (no exchange suffix)
        return None
    try:
        # The key goes in a header, not the URL, so it can never leak through
        # a logged URL or exception message.
        payload = await _get_json(
            "https://finnhub.io/api/v1/quote",
            params={"symbol": symbol},
            headers={"X-Finnhub-Token": key},
        )
        return parse_finnhub_quote(payload, symbol)
    except Exception as exc:
        logger.warning("Finnhub failed for %s: %s", symbol, _describe(exc))
        return None


def _chain(market: str):
    if market == PSX:
        return [_psx_portal, lambda s: _yahoo(s, PSX)]
    return [_finnhub, lambda s: _yahoo(s, GLOBAL)]


# ═══════════════════════════════════════════════════════════════════════════════
# QUOTE LOOKUP WITH CACHE
# ═══════════════════════════════════════════════════════════════════════════════

_cache: dict[tuple[str, str], tuple[float, Quote | None]] = {}


def _is_stale(quote: Quote) -> bool:
    return quote.as_of is not None and datetime.now(timezone.utc) - quote.as_of > MAX_AGE


async def get_quote(symbol: str, market: str) -> Quote | None:
    symbol = symbol.strip().upper()
    if not _SYMBOL_RE.match(symbol):
        return None

    key = (market, symbol)
    hit = _cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]

    for provider in _chain(market):
        quote = await provider(symbol)
        if quote and _is_stale(quote):
            logger.warning(
                "%s price for %s is from %s, too old to call current; ignoring it",
                quote.source, symbol, quote.as_of.strftime("%Y-%m-%d"),
            )
            continue
        if quote:
            _cache[key] = (time.monotonic() + QUOTE_TTL, quote)
            return quote

    _cache[key] = (time.monotonic() + FAIL_TTL, None)
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# TURNING TEXT INTO SYMBOLS
# ═══════════════════════════════════════════════════════════════════════════════

# Well-known foreign companies, matched as whole words. Deliberately limited
# to names that are rarely used in any other sense ("visa", "meta", "oracle"
# are left out because they appear in ordinary sentences).
GLOBAL_NAMES: dict[str, str] = {
    "apple": "AAPL", "tesla": "TSLA", "microsoft": "MSFT", "amazon": "AMZN",
    "google": "GOOGL", "alphabet": "GOOGL", "facebook": "META",
    "meta platforms": "META", "nvidia": "NVDA", "netflix": "NFLX",
    "amd": "AMD", "intel corporation": "INTC", "coca-cola": "KO", "coca cola": "KO",
    "pepsico": "PEP", "pepsi": "PEP", "mcdonalds": "MCD", "mcdonald's": "MCD",
    "nike": "NKE", "walmart": "WMT", "disney": "DIS", "mastercard": "MA",
    "jpmorgan": "JPM", "boeing": "BA", "starbucks": "SBUX", "paypal": "PYPL",
    "spotify": "SPOT", "adobe": "ADBE", "salesforce": "CRM", "pfizer": "PFE",
    "johnson & johnson": "JNJ", "exxon": "XOM", "exxonmobil": "XOM",
    "chevron": "CVX", "berkshire hathaway": "BRK-B", "toyota": "TM",
}

_NAME_RES = {
    name: re.compile(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])")
    for name in GLOBAL_NAMES
}
_CASHTAG_RE = re.compile(r"\$([A-Za-z][A-Za-z0-9\-]{0,5}(?:\.[A-Za-z]{1,2})?)(?![A-Za-z0-9])")


def mentions_global_company(text: str) -> bool:
    """True when the text names a company in GLOBAL_NAMES or uses a $TICKER."""
    lower = text.lower()
    return bool(_CASHTAG_RE.search(text)) or any(r.search(lower) for r in _NAME_RES.values())


def resolve_symbols(text: str) -> list[tuple[str, str]]:
    """
    Find the stocks a message is about. Returns up to MAX_SYMBOLS (symbol, market).

      1. a company in the built-in KMI database       -> PSX ticker
      2. a $CASHTAG ($AAPL, $LUCK)                     -> PSX if it is a known PSX ticker
      3. a well-known foreign company name (Apple...)  -> GLOBAL ticker
    """
    from app.services.web_search import KMI_SHARIAH_DB, lookup_shariah_status

    found: list[tuple[str, str]] = []

    def add(symbol: str, market: str):
        pair = (symbol, market)
        if pair not in found and len(found) < MAX_SYMBOLS:
            found.append(pair)

    _, data = lookup_shariah_status(text)
    if data:
        add(data["ticker"].upper(), PSX)

    psx_tickers = {d["ticker"].upper() for d in KMI_SHARIAH_DB.values()}
    for match in _CASHTAG_RE.finditer(text):
        symbol = match.group(1).upper()
        add(symbol, PSX if symbol in psx_tickers else GLOBAL)

    lower = text.lower()
    for name, symbol in GLOBAL_NAMES.items():
        if _NAME_RES[name].search(lower):
            add(symbol, GLOBAL)

    return found


async def search_equity(name: str) -> tuple[str, str] | None:
    """
    Last-resort lookup for a company name we don't know, via Yahoo's search
    endpoint. Only accepts stocks/ETFs whose name contains the query's first
    word, so "Bitcoin" or "Meezan Islamic Fund" never turns into a random stock.
    """
    words = re.findall(r"[a-z0-9]+", name.lower())
    if not words or len(words[0]) < 3:
        return None
    try:
        payload = await _get_json(
            "https://query2.finance.yahoo.com/v1/finance/search",
            params={"q": name.strip()[:60], "quotesCount": 5, "newsCount": 0},
        )
        for item in payload.get("quotes", []):
            if item.get("quoteType") not in ("EQUITY", "ETF"):
                continue
            label = f"{item.get('shortname', '')} {item.get('longname', '')}".lower()
            symbol = str(item.get("symbol", "")).upper()
            if words[0] in label and _SYMBOL_RE.match(symbol):
                if symbol.endswith(".KA"):
                    return symbol[:-3], PSX
                return symbol, GLOBAL
    except Exception as exc:
        logger.warning("Yahoo search failed for %r: %s", name[:40], _describe(exc))
    return None


async def get_quotes_for_text(text: str) -> list[Quote]:
    """Quotes for every stock the text clearly names. Empty list if none."""
    pairs = resolve_symbols(text)
    if not pairs:
        return []
    results = await asyncio.gather(*(get_quote(s, m) for s, m in pairs))
    return [q for q in results if q]


async def get_quotes_for_query(query: str) -> list[Quote]:
    """
    Like get_quotes_for_text(), but for the screener where the whole input is
    an investment name: when nothing is recognised, ask Yahoo's search.
    """
    quotes = await get_quotes_for_text(query)
    if quotes or resolve_symbols(query):
        return quotes

    from app.services.web_search import _PRODUCT_WORDS
    if set(re.findall(r"[a-z0-9]+", query.lower())) & _PRODUCT_WORDS:
        return []        # a fund, sukuk, account... not a listed company

    found = await search_equity(query)
    if not found:
        return []
    quote = await get_quote(*found)
    return [quote] if quote else []


# ═══════════════════════════════════════════════════════════════════════════════
# FORMATTING
# ═══════════════════════════════════════════════════════════════════════════════

def _when(quote: Quote) -> str:
    """When the price is from, as a phrase that reads on its own."""
    if quote.as_of is None:
        return "latest traded price, time not provided by the source"
    text = "as of " + quote.as_of.strftime("%d %b %Y %H:%M UTC")
    if datetime.now(timezone.utc) - quote.as_of > timedelta(days=2):
        text += " (last available price; the market has been closed since)"
    return text


def _move(quote: Quote) -> str:
    if quote.change is None or quote.change_pct is None:
        return ""
    return f"{quote.change:+,.2f} ({quote.change_pct:+.2f}%)"


def format_quotes_context(quotes: list[Quote]) -> str:
    """Context block for the chatbot. Empty string when there are no quotes."""
    if not quotes:
        return ""
    lines = ["=== LIVE STOCK QUOTES (from price APIs) ==="]
    for q in quotes:
        label = f"{q.name} ({q.symbol})" if q.name else q.symbol
        parts = [f"{label}: {q.currency} {q.price:,.2f}"]
        if _move(q):
            parts.append(f"change {_move(q)}")
        parts.append(_when(q))
        parts.append(f"source: {q.source}")
        lines.append(" | ".join(parts))
    lines += [
        "",
        "INSTRUCTION: Quote these prices exactly, with their currency, time and source.",
        "Say the price may be delayed or the market may be closed. These are the ONLY",
        "prices you have: never state or estimate a price for any other stock.",
        "=== END LIVE STOCK QUOTES ===",
    ]
    return "\n".join(lines)


def format_price_line(quote: Quote) -> str:
    """One short sentence for the screener result."""
    move = f", {_move(quote)}" if _move(quote) else ""
    return (
        f"Latest price: {quote.symbol} {quote.currency} {quote.price:,.2f}{move} "
        f"({_when(quote)}; {quote.source}; may be delayed)."
    )


# ═══════════════════════════════════════════════════════════════════════════════
# COMMAND-LINE CHECK
# ═══════════════════════════════════════════════════════════════════════════════

async def _check(args: list[str]) -> None:
    for arg in args:
        prefix, _, rest = arg.partition(":")
        if rest and prefix.lower() in ("psx", "us", "global"):
            market = PSX if prefix.lower() == "psx" else GLOBAL
            quotes = [q] if (q := await get_quote(rest, market)) else []
        elif _SYMBOL_RE.match(arg) and arg.isupper():
            # A bare ticker: PSX if it is one of ours, otherwise a foreign stock.
            from app.services.web_search import KMI_SHARIAH_DB
            is_psx = arg in {d["ticker"].upper() for d in KMI_SHARIAH_DB.values()}
            quotes = [q] if (q := await get_quote(arg, PSX if is_psx else GLOBAL)) else []
        else:
            quotes = await get_quotes_for_query(arg)
        print(f"\n{arg}")
        if not quotes:
            print("  no quote (see the warnings above for the reason)")
        for q in quotes:
            print("  " + format_price_line(q))


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    asyncio.run(_check(sys.argv[1:] or ["LUCK", "AAPL"]))
