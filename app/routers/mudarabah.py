from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from app.models.schemas import MudarabahRequest, MudarabahResponse
from app.services.mudarabah_engine import run_mudarabah_simulation, get_monthly_growth

router = APIRouter(prefix="/mudarabah", tags=["Mudarabah"])
templates = Jinja2Templates(directory="frontend/templates")


@router.post("/simulate", response_model=MudarabahResponse)
async def simulate(request: MudarabahRequest):
    """
    Runs the three-scenario Mudarabah simulation.
    Called by the simulator page via fetch() — returns JSON.
    """
    return run_mudarabah_simulation(request)


@router.post("/growth-chart")
async def growth_chart(request: MudarabahRequest):
    """
    Returns month-by-month cumulative profit data for the line chart.
    Separate endpoint so the bar chart and line chart can load independently.
    """
    months       = request.period_months
    monthly_data = get_monthly_growth(request.principal, request.profit_rate, months)
    labels       = [f"Month {m}" for m in range(1, months + 1)]

    return {
        "labels":  labels,
        "data":    monthly_data,
        "months":  months,
    }
