from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.database.crud import save_screener_result, get_screener_history
from app.database import models
from app.models.schemas import ScreenerRequest, ScreenerResponse
from app.services.rag_service import rag_service
from app.services.llm_client import screen_investment
from app.dependencies import get_current_user

router = APIRouter(prefix="/screener", tags=["Screener"])


@router.post("/check", response_model=ScreenerResponse)
async def check_investment(
    request: ScreenerRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    context = rag_service.build_context(
        f"halal haram shariah screening {request.query}",
        top_k=5,
    )
    parsed = screen_investment(request.query, context)
    save_screener_result(
        db,
        query       = request.query,
        verdict     = parsed["verdict"],
        explanation = parsed["reason"],
        user_id     = current_user.id,
    )
    return ScreenerResponse(
        query       = request.query,
        verdict     = parsed["verdict"],
        explanation = parsed["reason"],
        confidence  = parsed["confidence"],
    )


@router.get("/history")
async def screener_history(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    records = get_screener_history(db, user_id=current_user.id, limit=10)
    return [
        {
            "id":          r.id,
            "query":       r.query,
            "verdict":     r.verdict,
            "screened_at": r.screened_at.strftime("%d %b %Y, %I:%M %p"),
        }
        for r in records
    ]


@router.get("/preloaded")
async def preloaded_funds():
    return [
        {"name": "Meezan Islamic Fund", "type": "Equity", "verdict": "HALAL", "confidence": "HIGH",
         "reason": "Managed by Al Meezan Investment with full Shariah supervision. Invests only in PSX Shariah-compliant stocks. Certified by a qualified Shariah Supervisory Board."},
        {"name": "Al-Ameen Islamic Aggressive Income Fund", "type": "Income", "verdict": "HALAL", "confidence": "HIGH",
         "reason": "UBL Fund Managers Islamic product. Invests in Sukuk and Shariah-compliant money market instruments only."},
        {"name": "NBP Islamic Equity Fund", "type": "Equity", "verdict": "HALAL", "confidence": "HIGH",
         "reason": "NBP Funds Islamic product screened against PSX Shariah-compliant list. Supervised by a registered Shariah Advisor."},
        {"name": "HBL Islamic Money Market Fund", "type": "Money Market", "verdict": "HALAL", "confidence": "HIGH",
         "reason": "Invests only in short-term Shariah-compliant instruments and government Ijarah Sukuk. No interest-bearing instruments."},
        {"name": "MCB Pakistan Stock Market Fund", "type": "Equity", "verdict": "DOUBTFUL", "confidence": "MEDIUM",
         "reason": "Conventional fund — not Shariah-screened. May hold stocks of conventional banks. Prefer MCB Islamic Fund instead."},
        {"name": "National Savings Regular Income Certificates", "type": "Government", "verdict": "HARAM", "confidence": "HIGH",
         "reason": "Pays a fixed predetermined interest rate which is Riba. Not permissible under Islamic law. Use government Ijarah Sukuk instead."},
    ]
