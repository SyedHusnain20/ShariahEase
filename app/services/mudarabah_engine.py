import numpy as np
from app.models.schemas import MudarabahRequest, MudarabahResponse, ScenarioResult


# Scenario multipliers applied to the user's expected profit rate
# e.g. if user expects 12% → optimistic = 12*1.3 = 15.6%
SCENARIO_MULTIPLIERS = {
    "Optimistic":   1.30,
    "Moderate":     1.00,
    "Pessimistic":  0.55,
}


def run_mudarabah_simulation(request: MudarabahRequest) -> MudarabahResponse:
    """
    Simulates a Mudarabah (profit-sharing) contract under three scenarios.

    In a Mudarabah:
    - Rabb-ul-Maal  (investor) provides capital
    - Mudarib       (bank/entrepreneur) provides skill & management
    - Profit is split by agreed ratio — loss falls on investor only

    Formula per scenario:
        gross_profit    = principal × (annual_rate × multiplier) × (months/12)
        investor_profit = gross_profit × (investor_share / 100)
        bank_profit     = gross_profit × (bank_share / 100)
        total_return    = principal + investor_profit
    """
    principal      = request.principal
    annual_rate    = request.profit_rate / 100
    investor_share = request.investor_share
    bank_share     = 100 - investor_share
    months         = request.period_months
    period_years   = months / 12

    scenarios = []

    for label, multiplier in SCENARIO_MULTIPLIERS.items():
        effective_rate  = annual_rate * multiplier
        gross_profit    = round(principal * effective_rate * period_years, 2)

        investor_profit = round(gross_profit * (investor_share / 100), 2)
        bank_profit     = round(gross_profit * (bank_share / 100), 2)
        total_return    = round(principal + investor_profit, 2)
        monthly_return  = round(investor_profit / months, 2)

        scenarios.append(ScenarioResult(
            label           = label,
            gross_profit    = gross_profit,
            investor_profit = investor_profit,
            bank_profit     = bank_profit,
            total_return    = total_return,
            monthly_return  = monthly_return,
        ))

    return MudarabahResponse(
        principal      = principal,
        investor_share = investor_share,
        bank_share     = bank_share,
        period_months  = months,
        scenarios      = scenarios,
    )


def get_monthly_growth(principal: float, annual_rate: float, months: int) -> list:
    """
    Returns month-by-month cumulative investor profit for the Chart.js line chart.
    Uses the moderate (1.0×) scenario only.
    """
    monthly_rate = annual_rate / 100 / 12
    cumulative   = []
    for m in range(1, months + 1):
        profit = round(principal * monthly_rate * m, 2)
        cumulative.append(profit)
    return cumulative
