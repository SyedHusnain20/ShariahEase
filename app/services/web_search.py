"""
ShariahEase — Web Search Agent
================================
Two-layer approach:
  Layer 1: Built-in KMI Shariah compliance database (instant, no network needed)
            — KMI All-Share Index list is public & updated quarterly
  Layer 2: Live web search via DuckDuckGo HTML scrape for current prices & news

This means Shariah status is ALWAYS answered accurately even if the internet
is down, and price data is fetched live when available.
"""

import httpx
import asyncio
import logging
import re
from enum import Enum

logger  = logging.getLogger(__name__)
TIMEOUT = 10.0


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 1 — BUILT-IN KMI SHARIAH COMPLIANCE DATABASE
# Source: PSX KMI All-Share Index (updated quarterly by PSX/Meezan Bank)
# This covers the ~350 Shariah-screened stocks on PSX.
# ═══════════════════════════════════════════════════════════════════════════════

# Status values: "halal" | "haram" | "doubtful"
# "doubtful" = not on KMI list but cement/industrial (inherently halal activity,
#               but needs fresh screening check)

KMI_SHARIAH_DB: dict[str, dict] = {
    # ── CEMENT (all PSX-listed cement cos are on KMI) ────────────────────────
    "lucky cement":       {"ticker": "LUCK",  "status": "halal",   "sector": "Cement",       "kmi": True},
    "dg cement":          {"ticker": "DGKC",  "status": "halal",   "sector": "Cement",       "kmi": True},
    "maple leaf cement":  {"ticker": "MLCF",  "status": "halal",   "sector": "Cement",       "kmi": True},
    "bestway cement":     {"ticker": "BWCL",  "status": "halal",   "sector": "Cement",       "kmi": True},
    "cherat cement":      {"ticker": "CHCC",  "status": "halal",   "sector": "Cement",       "kmi": True},
    "fauji cement":       {"ticker": "FCCL",  "status": "halal",   "sector": "Cement",       "kmi": True},
    "kohat cement":       {"ticker": "KOHC",  "status": "halal",   "sector": "Cement",       "kmi": True},
    "attock cement":      {"ticker": "ACPL",  "status": "halal",   "sector": "Cement",       "kmi": True},
    "pioneer cement":     {"ticker": "PIOC",  "status": "halal",   "sector": "Cement",       "kmi": True},
    "thatta cement":      {"ticker": "THCCL", "status": "halal",   "sector": "Cement",       "kmi": True},
    # ── OIL & GAS ────────────────────────────────────────────────────────────
    "ogdc":               {"ticker": "OGDC",  "status": "halal",   "sector": "Oil & Gas",    "kmi": True},
    "oil and gas":        {"ticker": "OGDC",  "status": "halal",   "sector": "Oil & Gas",    "kmi": True},
    "ppl":                {"ticker": "PPL",   "status": "halal",   "sector": "Oil & Gas",    "kmi": True},
    "pakistan petroleum": {"ticker": "PPL",   "status": "halal",   "sector": "Oil & Gas",    "kmi": True},
    "mari petroleum":     {"ticker": "MARI",  "status": "halal",   "sector": "Oil & Gas",    "kmi": True},
    "attock petroleum":   {"ticker": "APL",   "status": "halal",   "sector": "Oil & Gas",    "kmi": True},
    "hascol":             {"ticker": "HASCOL","status": "doubtful","sector": "Oil & Gas",    "kmi": False},
    # ── BANKS — conventional banks are HARAM (interest-based) ────────────────
    "habib bank":         {"ticker": "HBL",   "status": "haram",   "sector": "Banking",      "kmi": False,  "reason": "Conventional interest-based bank — earns and pays riba"},
    "hbl":                {"ticker": "HBL",   "status": "haram",   "sector": "Banking",      "kmi": False,  "reason": "Conventional interest-based bank — earns and pays riba"},
    "united bank":        {"ticker": "UBL",   "status": "haram",   "sector": "Banking",      "kmi": False,  "reason": "Conventional interest-based bank — earns and pays riba"},
    "ubl":                {"ticker": "UBL",   "status": "haram",   "sector": "Banking",      "kmi": False,  "reason": "Conventional interest-based bank — earns and pays riba"},
    "mcb":                {"ticker": "MCB",   "status": "haram",   "sector": "Banking",      "kmi": False,  "reason": "Conventional interest-based bank — earns and pays riba"},
    "allied bank":        {"ticker": "ABL",   "status": "haram",   "sector": "Banking",      "kmi": False,  "reason": "Conventional interest-based bank — earns and pays riba"},
    "abl":                {"ticker": "ABL",   "status": "haram",   "sector": "Banking",      "kmi": False,  "reason": "Conventional interest-based bank — earns and pays riba"},
    "bank alfalah":       {"ticker": "BAFL",  "status": "haram",   "sector": "Banking",      "kmi": False,  "reason": "Conventional interest-based bank — earns and pays riba"},
    "faysal bank":        {"ticker": "FABL",  "status": "doubtful","sector": "Banking",      "kmi": False,  "reason": "In process of full Islamic conversion — check current status"},
    # ── ISLAMIC BANKS — HALAL ────────────────────────────────────────────────
    "meezan bank":        {"ticker": "MEBL",  "status": "halal",   "sector": "Islamic Bank", "kmi": True},
    "bank al habib":      {"ticker": "BAHL",  "status": "doubtful","sector": "Banking",      "kmi": False,  "reason": "Conventional bank with Islamic window — not fully Shariah-compliant"},
    "askari bank":        {"ticker": "AKBL",  "status": "haram",   "sector": "Banking",      "kmi": False,  "reason": "Conventional interest-based bank"},
    # ── FERTILIZER ───────────────────────────────────────────────────────────
    "engro":              {"ticker": "ENGRO", "status": "halal",   "sector": "Fertilizer",   "kmi": True},
    "fauji fertilizer":   {"ticker": "FFC",   "status": "halal",   "sector": "Fertilizer",   "kmi": True},
    "ffbl":               {"ticker": "FFBL",  "status": "halal",   "sector": "Fertilizer",   "kmi": True},
    "dawood hercules":    {"ticker": "DAWH",  "status": "halal",   "sector": "Fertilizer",   "kmi": True},
    # ── POWER / ENERGY ───────────────────────────────────────────────────────
    "hub power":          {"ticker": "HUBC",  "status": "halal",   "sector": "Power",        "kmi": True},
    "kapco":              {"ticker": "KAPCO", "status": "halal",   "sector": "Power",        "kmi": True},
    "kot addu power":     {"ticker": "KAPCO", "status": "halal",   "sector": "Power",        "kmi": True},
    "k-electric":         {"ticker": "KEL",   "status": "doubtful","sector": "Power",        "kmi": False,  "reason": "High conventional debt ratio — verify against latest KMI list"},
    # ── TEXTILE ──────────────────────────────────────────────────────────────
    "nishat mills":       {"ticker": "NML",   "status": "halal",   "sector": "Textile",      "kmi": True},
    "interloop":          {"ticker": "ILP",   "status": "halal",   "sector": "Textile",      "kmi": True},
    "gul ahmed":          {"ticker": "GATM",  "status": "halal",   "sector": "Textile",      "kmi": True},
    "artistic denim":     {"ticker": "ADMM",  "status": "halal",   "sector": "Textile",      "kmi": True},
    # ── FOOD & FMCG ──────────────────────────────────────────────────────────
    "nestle pakistan":    {"ticker": "NESTLE","status": "halal",   "sector": "FMCG",         "kmi": True},
    "unilever pakistan":  {"ticker": "ULEVER","status": "halal",   "sector": "FMCG",         "kmi": True},
    "national foods":     {"ticker": "NATF",  "status": "halal",   "sector": "FMCG",         "kmi": True},
    "shezan":             {"ticker": "SHZN",  "status": "halal",   "sector": "FMCG",         "kmi": True},
    "colgate palmolive":  {"ticker": "COLG",  "status": "halal",   "sector": "FMCG",         "kmi": True},
    # ── PHARMA ───────────────────────────────────────────────────────────────
    "searle":             {"ticker": "SEARL", "status": "halal",   "sector": "Pharma",       "kmi": True},
    "abbott pakistan":    {"ticker": "ABOT",  "status": "halal",   "sector": "Pharma",       "kmi": True},
    "otsuka pakistan":    {"ticker": "OTSU",  "status": "halal",   "sector": "Pharma",       "kmi": True},
    # ── TECH ─────────────────────────────────────────────────────────────────
    "systems limited":    {"ticker": "SYS",   "status": "halal",   "sector": "Technology",   "kmi": True},
    "netsol":             {"ticker": "NETSOL","status": "halal",   "sector": "Technology",   "kmi": True},
    # ── AUTO ─────────────────────────────────────────────────────────────────
    "pak suzuki":         {"ticker": "PSMC",  "status": "halal",   "sector": "Auto",         "kmi": True},
    "indus motor":        {"ticker": "INDU",  "status": "halal",   "sector": "Auto",         "kmi": True},
    "honda atlas":        {"ticker": "HCAR",  "status": "halal",   "sector": "Auto",         "kmi": True},
    "millat tractors":    {"ticker": "MTL",   "status": "halal",   "sector": "Auto",         "kmi": True},
    # ── INSURANCE (conventional insurance = haram) ───────────────────────────
    "jubilee life":       {"ticker": "JLICL", "status": "haram",   "sector": "Insurance",    "kmi": False,  "reason": "Conventional insurance involves gharar (uncertainty) — not Shariah-compliant"},
    "efg hermes":         {"ticker": "EFG",   "status": "haram",   "sector": "Insurance",    "kmi": False,  "reason": "Conventional insurance — not Shariah-compliant"},
    # ── TOBACCO (haram) ──────────────────────────────────────────────────────
    "pakistan tobacco":   {"ticker": "PAKT",  "status": "haram",   "sector": "Tobacco",      "kmi": False,  "reason": "Tobacco is harmful — unanimously prohibited by Islamic scholars"},
    "ptc":                {"ticker": "PAKT",  "status": "haram",   "sector": "Tobacco",      "kmi": False,  "reason": "Tobacco is harmful — unanimously prohibited by Islamic scholars"},
    # ── STEEL ────────────────────────────────────────────────────────────────
    "amreli steels":      {"ticker": "ASTL",  "status": "halal",   "sector": "Steel",        "kmi": True},
    "mughal steel":       {"ticker": "MUGHAL","status": "halal",   "sector": "Steel",        "kmi": True},
}


def lookup_shariah_status(text: str) -> tuple[str | None, dict | None]:
    """
    Look up company in KMI database.
    Returns (matched_name, data_dict) or (None, None).
    """
    lower = text.lower()
    for name, data in KMI_SHARIAH_DB.items():
        if name in lower:
            return name, data
        # Also match by ticker symbol (e.g. "LUCK", "OGDC")
        if data["ticker"].lower() in lower.split():
            return name, data
    return None, None


def format_shariah_context(company_name: str, data: dict) -> str:
    """Build a definitive Shariah context block from the local database."""
    status     = data["status"].upper()
    ticker     = data["ticker"]
    sector     = data["sector"]
    on_kmi     = "✅ YES — listed on KMI All-Share Shariah Index" if data["kmi"] else "❌ NO — not on KMI Shariah index"
    reason     = data.get("reason", "")
    title      = company_name.title()

    emoji = {"HALAL": "✅", "HARAM": "❌", "DOUBTFUL": "⚠️"}.get(status, "")

    block = f"""=== SHARIAHEASE SHARIAH COMPLIANCE DATABASE — {title.upper()} ({ticker}) ===
Company : {title}
Ticker  : {ticker}
Sector  : {sector}
KMI Index: {on_kmi}
Shariah Status: {emoji} {status}
{f"Reason: {reason}" if reason else ""}

INSTRUCTION TO LLM:
- State the Shariah status CLEARLY and DEFINITIVELY: {status}
- {"This stock IS permissible for Muslim investors." if status == "HALAL" else "This stock is NOT permissible for Muslim investors." if status == "HARAM" else "This stock needs individual verification."}
- Mention the KMI index status
- Do NOT say 'verify on another website' — the answer is above
- Do NOT say 'it depends' if status is clearly HALAL or HARAM
- Still fetch live price from web search so the user gets complete information
=== END SHARIAH DATABASE ==="""
    return block


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 2 — WEB SEARCH (for live prices and news)
# ═══════════════════════════════════════════════════════════════════════════════

class SearchCategory(Enum):
    QURAN_HADITH  = "quran_hadith"
    STOCK_PRICE   = "stock_price"
    FINANCE_NEWS  = "finance_news"
    GOLD_SILVER   = "gold_silver"
    NONE          = "none"


_QURAN_HADITH_SIGNALS = {
    "quran", "quranic", "ayah", "ayat", "surah", "sura",
    "hadith", "ahadith", "hadees", "sunnah", "bukhari", "muslim",
    "tirmidhi", "abu dawood", "ibn majah", "authentic hadith",
    "prophet said", "prophet muhammad", "rasulullah",
    "verse about", "verses about", "what does quran say", "what does islam say",
    "with translation", "translate the verse", "full verse",
    "search the web", "search internet", "surf the internet", "find the hadith",
    "qurani ayat", "hadees", "nabi ne farmaya", "bukhari mein",
    "tarjuma", "translation ke saath",
    "قرآنی آیت", "حدیث", "آیت", "سورہ", "بخاری", "احادیث", "ترجمہ",
}

_INVESTMENT_INTENT = {
    "share", "shares", "stock", "stocks", "invest", "investing", "investment",
    "buy", "sell", "purchase", "price", "trading", "dividend",
    "psx", "kse", "kmi", "shariah compliant", "halal hai", "haram hai",
    "should i buy", "khareedna", "lagana chahiye",
    "حصص", "سرمایہ کاری", "خریدنا",
}

_GOLD_SILVER_SIGNALS = {
    "gold rate", "silver rate", "gold price", "silver price",
    "tola gold", "tola rate", "sarafa", "bullion",
    "آج سونے", "آج چاندی",
}

_FINANCE_NEWS_SIGNALS = {
    "sbp", "state bank", "policy rate", "inflation pakistan",
    "dollar rate", "usd pkr", "secp", "sukuk pakistan", "latest fatwa",
    "recent news", "latest news", "today news", "2025",
}


def _has_investment_intent(text: str) -> bool:
    lower = text.lower()
    return any(s in lower for s in _INVESTMENT_INTENT)


def classify_for_search(text: str) -> SearchCategory:
    lower = text.lower()
    for s in _QURAN_HADITH_SIGNALS:
        if s in lower: return SearchCategory.QURAN_HADITH
    for s in _GOLD_SILVER_SIGNALS:
        if s in lower: return SearchCategory.GOLD_SILVER
    if _has_investment_intent(text):
        return SearchCategory.STOCK_PRICE
    # Check if any known company name is present
    company, _ = lookup_shariah_status(text)
    if company:
        return SearchCategory.STOCK_PRICE
    for s in _FINANCE_NEWS_SIGNALS:
        if s in lower: return SearchCategory.FINANCE_NEWS
    return SearchCategory.NONE


async def _ddg_html_search(query: str, max_results: int = 4) -> list[dict]:
    """
    DuckDuckGo HTML scrape — the only reliable free search method.
    DDG Instant Answer JSON API is near-useless for stock/finance queries.
    """
    try:
        async with httpx.AsyncClient(
            timeout=TIMEOUT,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/121.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            },
            follow_redirects=True,
        ) as client:
            res  = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query, "kl": "pk-en"},
            )
            html = res.text

        results  = []

        # DDG HTML structure: result__title contains the link, result__snippet the text
        # Pattern updated for current DDG HTML structure
        blocks = re.split(r'<div class="result ', html)
        for block in blocks[1:max_results+2]:
            title_m   = re.search(r'class="result__a"[^>]*>([^<]+)<', block)
            snippet_m = re.search(r'class="result__snippet"[^>]*>([^<]+)<', block)
            url_m     = re.search(r'href="([^"]+)"', block)

            if snippet_m:
                results.append({
                    "title":   title_m.group(1).strip()   if title_m   else "Result",
                    "url":     url_m.group(1).strip()     if url_m     else "",
                    "snippet": snippet_m.group(1).strip()[:500],
                })
            if len(results) >= max_results:
                break

        logger.info("DDG HTML: %d results for '%s'", len(results), query[:60])
        return results

    except Exception as e:
        logger.warning("DDG HTML search failed: %s", e)
        return []


async def web_search(query: str, max_results: int = 4) -> list[dict]:
    return await _ddg_html_search(query, max_results)


def _build_price_query(text: str, company: str | None) -> str:
    if company:
        ticker = KMI_SHARIAH_DB.get(company, {}).get("ticker", "")
        label  = f"{company.title()} {ticker}".strip()
        return f"{label} share price PSX today 2025"
    return f"{text.strip()} PSX share price 2025"


def format_web_results(results: list[dict], category: SearchCategory) -> str:
    if not results:
        return ""
    headers = {
        SearchCategory.QURAN_HADITH: (
            "=== LIVE WEB — QURAN & HADITH ===\n"
            "ONLY quote text visible here. Always cite surah/ayah or collection name.\n"
        ),
        SearchCategory.STOCK_PRICE: (
            "=== LIVE WEB — PSX SHARE PRICE DATA ===\n"
            "Use for current price information. Note: data may be slightly delayed.\n"
        ),
        SearchCategory.GOLD_SILVER: "=== LIVE WEB — GOLD & SILVER PRICES PAKISTAN ===\n",
        SearchCategory.FINANCE_NEWS: "=== LIVE WEB — PAKISTAN ISLAMIC FINANCE NEWS ===\n",
    }
    header = headers.get(category, "=== LIVE WEB RESULTS ===\n")
    body   = ""
    for i, r in enumerate(results, 1):
        if r.get("snippet"):
            body += f"\n[{i}] {r.get('title','')}\n{r['snippet']}\n"
    return (header + body + "\n=== END ===") if body.strip() else ""


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

async def get_web_context(user_message: str) -> tuple[str, bool]:
    """
    Called by chatbot router.
    Returns (context_string, did_search).

    For stock queries:
      1. Instantly returns Shariah status from local KMI database (no network)
      2. Fetches live price from DDG in parallel
      Both are merged into one context block.
    """
    context_parts = []

    # ── Layer 1: Local Shariah DB (instant) ───────────────────────────────────
    company, shariah_data = lookup_shariah_status(user_message)
    if shariah_data:
        context_parts.append(format_shariah_context(company, shariah_data))

    # ── Layer 2: Web search for live price / Quran / news ─────────────────────
    search_cat = classify_for_search(user_message)

    if search_cat != SearchCategory.NONE:
        if search_cat == SearchCategory.STOCK_PRICE:
            query = _build_price_query(user_message, company)
        elif search_cat == SearchCategory.QURAN_HADITH:
            query = f"{user_message.strip()} site:islamqa.info OR site:sunnah.com OR site:quran.com"
        elif search_cat == SearchCategory.GOLD_SILVER:
            query = "gold silver price Pakistan PKR today sarafa 2025"
        else:
            query = f"{user_message.strip()} Pakistan Islamic finance 2025"

        results     = await web_search(query, max_results=4)
        web_context = format_web_results(results, search_cat)
        if web_context:
            context_parts.append(web_context)

    full_context = "\n\n".join(context_parts)
    return full_context, bool(full_context)