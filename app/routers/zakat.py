from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.database.crud import save_zakat_record, get_zakat_history
from app.models.schemas import ZakatRequest, ZakatResult
from app.services.zakat_engine import calculate_zakat
from app.services.metal_price import get_nisab_values

router = APIRouter(prefix="/zakat", tags=["Zakat"])
templates = Jinja2Templates(directory="frontend/templates")


@router.post("/calculate", response_model=ZakatResult)
async def zakat_calculate(request: ZakatRequest, db: Session = Depends(get_db)):
    nisab_data = await get_nisab_values()
    result = calculate_zakat(
        request,
        gold_price_per_gram   = nisab_data["gold_per_gram_pkr"],
        silver_price_per_gram = nisab_data["silver_per_gram_pkr"],
    )
    save_zakat_record(db, request, result)
    return result


@router.get("/nisab")
async def nisab_info():
    data = await get_nisab_values()
    return {
        "gold_nisab_pkr":      data["gold_nisab_pkr"],
        "silver_nisab_pkr":    data["silver_nisab_pkr"],
        "gold_per_gram_pkr":   data["gold_per_gram_pkr"],
        "silver_per_gram_pkr": data["silver_per_gram_pkr"],
        "usd_to_pkr":          data["usd_to_pkr"],
        "gold_grams":          data["gold_grams"],
        "silver_grams":        data["silver_grams"],
        "source":              data["source"],
    }


@router.get("/history")
async def zakat_history(db: Session = Depends(get_db)):
    records = get_zakat_history(db, limit=10)
    return [
        {
            "id":               r.id,
            "user_name":        r.user_name,
            "zakatable_wealth": r.zakatable_wealth,
            "zakat_due":        r.zakat_due,
            "is_zakat_due":     r.is_zakat_due,
            "calculated_at":    r.calculated_at.strftime("%d %b %Y, %I:%M %p"),
        }
        for r in records
    ]
