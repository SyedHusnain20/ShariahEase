from app.models.schemas import ZakatRequest, ZakatResult

# ── NISAB THRESHOLDS ──────────────────────────────────────────────────────────
# Gold  Nisab = 7.5 tola  = 87.48 grams
# Silver Nisab = 52.5 tola = 612.36 grams
# These gram weights are fixed Islamic values — only the PKR price changes.

GOLD_GRAMS   = 87.48
SILVER_GRAMS = 612.36

# Fallback prices in PKR if the metal API is unavailable (updated manually)
FALLBACK_GOLD_PER_GRAM   = 21000.0
FALLBACK_SILVER_PER_GRAM = 250.0

ZAKAT_RATE = 0.025  # 2.5% — fixed in all four Sunni madhabs


def get_nisab_pkr(rate: str = "gold",
                  gold_price_per_gram: float = None,
                  silver_price_per_gram: float = None) -> float:
    """
    Calculate the Nisab threshold in PKR.

    Scholars differ on which standard to use:
    - Gold   standard → higher threshold → fewer people pay
    - Silver standard → lower threshold  → more people pay (more cautious)

    We let the user choose; silver is the safer/more common choice in Pakistan.
    """
    if rate == "silver":
        price = silver_price_per_gram or FALLBACK_SILVER_PER_GRAM
        return round(SILVER_GRAMS * price, 2)
    else:
        price = gold_price_per_gram or FALLBACK_GOLD_PER_GRAM
        return round(GOLD_GRAMS * price, 2)


def calculate_zakat(
    request: ZakatRequest,
    gold_price_per_gram: float = None,
    silver_price_per_gram: float = None,
) -> ZakatResult:
    """
    Core Zakat calculation following standard Hanafi/Shafi'i rules:

    Zakatable wealth = (all zakatable assets) - (immediate debts & expenses)

    If zakatable_wealth >= Nisab  AND  one lunar year has passed (Hawl) →
    Zakat due = zakatable_wealth × 2.5%

    Note: We assume Hawl is satisfied (user confirms they have held
    these assets for one lunar year before calculating).
    """

    # ── ASSETS ────────────────────────────────────────────────────────────────
    total_assets = (
        request.cash
        + request.gold_value
        + request.silver_value
        + request.business_inventory
        + request.receivables
    )

    # ── DEDUCTIONS ────────────────────────────────────────────────────────────
    # Only immediate/short-term debts are deductible — long-term mortgage
    # instalments due THIS year only, not the full mortgage.
    total_deductions = request.debts + request.immediate_expenses

    # ── ZAKATABLE WEALTH ──────────────────────────────────────────────────────
    zakatable_wealth = max(0.0, total_assets - total_deductions)

    # ── NISAB CHECK ───────────────────────────────────────────────────────────
    nisab = get_nisab_pkr(
        rate=request.nisab_rate_used,
        gold_price_per_gram=gold_price_per_gram,
        silver_price_per_gram=silver_price_per_gram,
    )

    is_zakat_due = zakatable_wealth >= nisab

    # ── ZAKAT AMOUNT ──────────────────────────────────────────────────────────
    zakat_due = round(zakatable_wealth * ZAKAT_RATE, 2) if is_zakat_due else 0.0

    # ── BREAKDOWN (shown line-by-line in the results card) ────────────────────
    breakdown = {
        "cash":                round(request.cash, 2),
        "gold_value":          round(request.gold_value, 2),
        "silver_value":        round(request.silver_value, 2),
        "business_inventory":  round(request.business_inventory, 2),
        "receivables":         round(request.receivables, 2),
        "total_assets":        round(total_assets, 2),
        "debts":               round(request.debts, 2),
        "immediate_expenses":  round(request.immediate_expenses, 2),
        "total_deductions":    round(total_deductions, 2),
        "zakatable_wealth":    round(zakatable_wealth, 2),
        "nisab_threshold":     round(nisab, 2),
        "nisab_rate_used":     request.nisab_rate_used,
        "zakat_rate_pct":      2.5,
        "zakat_due":           zakat_due,
        "is_zakat_due":        is_zakat_due,
    }

    return ZakatResult(
        user_name        = request.user_name,
        total_assets     = round(total_assets, 2),
        total_deductions = round(total_deductions, 2),
        zakatable_wealth = round(zakatable_wealth, 2),
        nisab_threshold  = round(nisab, 2),
        zakat_due        = zakat_due,
        is_zakat_due     = is_zakat_due,
        breakdown        = breakdown,
    )
