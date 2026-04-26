import httpx
import os
import json
import time
from dotenv import load_dotenv

load_dotenv()

METAL_API_KEY = os.getenv("METAL_API_KEY", "")

# ── FALLBACK PRICES (PKR) ─────────────────────────────────
# Update these manually every few weeks if API key not set.
# As of early 2025 approximate PKR values:
FALLBACK_GOLD_PER_GRAM   = 21000.0   # PKR per gram of 24k gold
FALLBACK_SILVER_PER_GRAM = 250.0     # PKR per gram of silver

# Nisab weights (fixed Islamic values — never change)
GOLD_NISAB_GRAMS   = 87.48    # 7.5 tola
SILVER_NISAB_GRAMS = 612.36   # 52.5 tola

# USD to PKR fallback rate (updated manually)
FALLBACK_USD_TO_PKR = 278.0

# Simple in-memory cache — avoids hammering the API on every page load
_cache = {
    "gold_per_gram":   None,
    "silver_per_gram": None,
    "usd_to_pkr":      None,
    "fetched_at":      0,
}
CACHE_TTL_SECONDS = 3600  # refresh prices once per hour


def _cache_is_fresh() -> bool:
    return (
        _cache["gold_per_gram"] is not None
        and (time.time() - _cache["fetched_at"]) < CACHE_TTL_SECONDS
    )


async def _fetch_usd_to_pkr() -> float:
    """
    Fetch USD → PKR exchange rate from a free public API.
    No API key required.
    """
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            res = await client.get(
                "https://api.exchangerate-api.com/v4/latest/USD"
            )
            data = res.json()
            rate = data["rates"].get("PKR", FALLBACK_USD_TO_PKR)
            return float(rate)
    except Exception:
        return FALLBACK_USD_TO_PKR


async def _fetch_metal_prices_goldapi() -> dict:
    """
    Fetch gold & silver prices from goldapi.io (free tier).
    Returns prices in USD per troy ounce.
    1 troy ounce = 31.1035 grams
    """
    headers = {
        "x-access-token": METAL_API_KEY,
        "Content-Type":   "application/json",
    }
    prices = {}
    async with httpx.AsyncClient(timeout=8.0) as client:
        for metal in ["XAU", "XAG"]:  # XAU = gold, XAG = silver
            res  = await client.get(
                f"https://www.goldapi.io/api/{metal}/USD",
                headers=headers,
            )
            data = res.json()
            # price is per troy ounce in USD
            prices[metal] = float(data["price"])
    return prices


async def get_live_prices() -> dict:
    """
    Main entry point — returns gold & silver prices per gram in PKR.

    Priority:
    1. In-memory cache (if fresh)
    2. goldapi.io (if METAL_API_KEY is set)
    3. Hardcoded fallback values

    Always returns a valid dict — never raises an exception to the caller.
    """
    if _cache_is_fresh():
        return {
            "gold_per_gram":   _cache["gold_per_gram"],
            "silver_per_gram": _cache["silver_per_gram"],
            "usd_to_pkr":      _cache["usd_to_pkr"],
            "source":          "cache",
        }

    usd_to_pkr = await _fetch_usd_to_pkr()

    if METAL_API_KEY:
        try:
            prices = await _fetch_metal_prices_goldapi()

            troy_ounce_to_gram = 31.1035
            gold_usd_per_gram   = prices["XAU"] / troy_ounce_to_gram
            silver_usd_per_gram = prices["XAG"] / troy_ounce_to_gram

            gold_pkr_per_gram   = round(gold_usd_per_gram   * usd_to_pkr, 2)
            silver_pkr_per_gram = round(silver_usd_per_gram * usd_to_pkr, 2)
            source = "goldapi.io"

        except Exception:
            gold_pkr_per_gram   = FALLBACK_GOLD_PER_GRAM
            silver_pkr_per_gram = FALLBACK_SILVER_PER_GRAM
            source = "fallback"
    else:
        gold_pkr_per_gram   = FALLBACK_GOLD_PER_GRAM
        silver_pkr_per_gram = FALLBACK_SILVER_PER_GRAM
        source = "fallback"

    # Update cache
    _cache["gold_per_gram"]   = gold_pkr_per_gram
    _cache["silver_per_gram"] = silver_pkr_per_gram
    _cache["usd_to_pkr"]      = usd_to_pkr
    _cache["fetched_at"]      = time.time()

    return {
        "gold_per_gram":   gold_pkr_per_gram,
        "silver_per_gram": silver_pkr_per_gram,
        "usd_to_pkr":      usd_to_pkr,
        "source":          source,
    }


async def get_nisab_values() -> dict:
    """
    Returns both Nisab thresholds (gold & silver) in PKR,
    plus the raw per-gram prices and data source.
    """
    prices = await get_live_prices()

    gold_nisab_pkr   = round(GOLD_NISAB_GRAMS   * prices["gold_per_gram"],   2)
    silver_nisab_pkr = round(SILVER_NISAB_GRAMS * prices["silver_per_gram"], 2)

    return {
        "gold_nisab_pkr":        gold_nisab_pkr,
        "silver_nisab_pkr":      silver_nisab_pkr,
        "gold_per_gram_pkr":     prices["gold_per_gram"],
        "silver_per_gram_pkr":   prices["silver_per_gram"],
        "usd_to_pkr":            prices["usd_to_pkr"],
        "gold_grams":            GOLD_NISAB_GRAMS,
        "silver_grams":          SILVER_NISAB_GRAMS,
        "source":                prices["source"],
    }
