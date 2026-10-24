"""
Nisab detection and the live Nisab context block for the chatbot.

Moved out of app/routers/chatbot.py unchanged so the chat graph can use it
without importing a router.
"""

from app.services.metal_price import get_nisab_values

# Only queries specifically about thresholds/prices trigger live Nisab injection.
# General "zakat" questions must NOT be in this list — they get answered from RAG.
NISAB_KEYWORDS = [
    "نصاب", "nisab", "nisaab",
    "سونے کا نصاب", "چاندی کا نصاب",
    "gold nisab", "silver nisab",
    "sone ka nisab", "chandi ka nisab",
    "زکوٰۃ کی حد", "zakat limit", "zakat threshold",
    "how much gold", "how much silver",
    "تولہ", "tola",
]


def is_nisab_question(text: str) -> bool:
    t = text.lower()
    return any(k.lower() in t for k in NISAB_KEYWORDS)


async def get_live_nisab_block() -> tuple[str, dict]:
    """Returns (formatted context string, raw data dict). The dict is empty
    when live prices were unavailable and the approximate block was used."""
    try:
        data         = await get_nisab_values()
        gold_nisab   = int(data["gold_nisab_pkr"])
        silver_nisab = int(data["silver_nisab_pkr"])
        gold_gram    = int(data["gold_per_gram_pkr"])
        silver_gram  = int(data["silver_per_gram_pkr"])

        block = f"""
=== CURRENT LIVE NISAB DATA — USE THESE EXACT NUMBERS IN YOUR ANSWER ===
Gold price today     : PKR {gold_gram:,} per gram
Silver price today   : PKR {silver_gram:,} per gram

Gold Nisab  (7.5 tola = 87.48 grams) : PKR {gold_nisab:,}
Silver Nisab (52.5 tola = 612.36 grams): PKR {silver_nisab:,}

Zakat rate  : 2.5% of all zakatable wealth above Nisab
Data source : {data.get("source", "fallback")}

INSTRUCTION: Structure your answer as:
1. Brief definition of Nisab (1-2 sentences)
2. Gold Nisab: 7.5 tola = 87.48 grams = PKR {gold_nisab:,} TODAY
3. Silver Nisab: 52.5 tola = 612.36 grams = PKR {silver_nisab:,} TODAY
4. Zakat rate: 2.5%
5. One-line disclaimer that prices change daily
DO NOT say "check another website" — all data is provided above.
=== END LIVE NISAB DATA ===
""".strip()
        return block, data

    except Exception:
        block = """
=== NISAB DATA (APPROXIMATE — LIVE PRICE UNAVAILABLE) ===
Gold Nisab  (7.5 tola = 87.48 grams) : approximately PKR 18,00,000 to PKR 20,00,000
Silver Nisab (52.5 tola = 612.36 grams): approximately PKR 1,50,000 to PKR 2,00,000
Zakat rate: 2.5%
Note: Live prices temporarily unavailable. Figures are approximate.
=== END NISAB DATA ===
""".strip()
        return block, {}
