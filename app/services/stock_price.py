"""
ShariahEase — live stock prices from free sources.

No single free API covers both PSX and foreign stocks, so each market gets a
chain of sources and the first one that answers wins:

    PSX     1. PSX's own data portal   dps.psx.com.pk   (no key)
            2. Yahoo Finance chart     "<SYMBOL>.KA"     (no key)
    GLOBAL  1. Finnhub                 only if FINNHUB_API_KEY is set; free
                                       plan covers US-listed stocks only
            2. Yahoo Finance chart     (no key)

Things to know before relying on this:
  * Yahoo's endpoint is unofficial. It works without a key but is sometimes
    rate-limited (HTTP 429), especially from shared cloud IPs such as
    Hugging Face Spaces. A free Finnhub key makes US quotes much more reliable.
  * The PSX portal is PSX's public website data, not a documented API.
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
from datetime import datetime, timezone
from urllib.parse import quote as _urlquote

import httpx

logger = logging.getLogger(__name__)

TIMEOUT      = 6.0    # seconds per provider call
QUOTE_TTL    = 60     # seconds a good quote is reused (protects free-tier limits)
FAIL_TTL     = 30     # seconds before retrying a symbol every provider failed on
MAX_SYMBOLS  = 3      # quotes fetched for one message

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

def parse_psx_timeseries(payload, symbol: str) -> Quote | None:
    """
    dps.psx.com.pk/timeseries/int/<SYMBOL>
        {"status": 1, "message": "", "data": [[<epoch>, <price>, <volume>], ...]}
    Order is not assumed: the row with the latest timestamp is the quote.
    """
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None

    latest: tuple[float, float] | None = None
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        stamp, price = _num(row[0]), _num(row[1])
        if stamp and price and price > 0 and (latest is None or stamp > latest[0]):
            latest = (stamp, price)
    if latest is None:
        return None

    return Quote(
        symbol=symbol, market=PSX, price=latest[1], currency="PKR",
        source="PSX data portal", as_of=_utc(latest[0]),
    )


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

async def _psx_portal(symbol: str) -> Quote | None:
    try:
        payload = await _get_json(
            f"https://dps.psx.com.pk/timeseries/int/{_urlquote(symbol)}",
            headers={"Referer": "https://dps.psx.com.pk/"},
        )
        quote = parse_psx_timeseries(payload, symbol)
        if quote is None:
            logger.warning("PSX portal: no usable price for %s (format changed or symbol unknown)", symbol)
        return quote
    except Exception as exc:
        logger.warning("PSX portal failed for %s: %s", symbol, _describe(exc))
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
    return quote.as_of.strftime("%d %b %Y %H:%M UTC") if quote.as_of else "time not available"


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
        parts.append(f"as of {_when(q)}")
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
        f"(as of {_when(quote)}, {quote.source}; may be delayed)."
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
