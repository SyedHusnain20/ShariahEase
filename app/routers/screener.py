from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.database.crud import save_screener_result, get_screener_history
from app.models.schemas import ScreenerRequest, ScreenerResponse
from app.services.rag_service import rag_service
from app.services.groq_client import screen_investment

router = APIRouter(prefix="/screener", tags=["Screener"])


@router.post("/check", response_model=ScreenerResponse)
async def check_investment(request: ScreenerRequest, db: Session = Depends(get_db)):
    """
    Main screening endpoint.
    1. Retrieves relevant Shariah criteria from FAISS
    2. Calls Groq with the criteria + query
    3. Parses the structured verdict
    4. Saves to DB history
    5. Returns the result
    """
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
    )

    return ScreenerResponse(
        query       = request.query,
        verdict     = parsed["verdict"],
        explanation = parsed["reason"],
        confidence  = parsed["confidence"],
    )


@router.get("/history")
async def screener_history(db: Session = Depends(get_db)):
    """Returns last 10 screening queries."""
    records = get_screener_history(db, limit=10)
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
    """
    Returns a static list of pre-screened Pakistani mutual funds.
    Shown as quick-access cards on the screener page.
    """
    return [
        {
            "name":       "Meezan Islamic Fund",
            "type":       "Equity",
            "verdict":    "HALAL",
            "confidence": "HIGH",
            "reason":     "Managed by Al Meezan Investment with full Shariah supervision. "
                          "Invests only in PSX Shariah-compliant stocks. "
                          "Certified by a qualified Shariah Supervisory Board.",
        },
        {
            "name":       "Al-Ameen Islamic Aggressive Income Fund",
            "type":       "Income",
            "verdict":    "HALAL",
            "confidence": "HIGH",
            "reason":     "UBL Fund Managers Islamic product. Invests in Sukuk and "
                          "Shariah-compliant money market instruments only.",
        },
        {
            "name":       "NBP Islamic Equity Fund",
            "type":       "Equity",
            "verdict":    "HALAL",
            "confidence": "HIGH",
            "reason":     "NBP Funds Islamic product screened against PSX Shariah-compliant "
                          "list. Supervised by a registered Shariah Advisor.",
        },
        {
            "name":       "HBL Islamic Money Market Fund",
            "type":       "Money Market",
            "verdict":    "HALAL",
            "confidence": "HIGH",
            "reason":     "Invests only in short-term Shariah-compliant instruments "
                          "and government Ijarah Sukuk. No interest-bearing instruments.",
        },
        {
            "name":       "MCB Pakistan Stock Market Fund",
            "type":       "Equity",
            "verdict":    "DOUBTFUL",
            "confidence": "MEDIUM",
            "reason":     "Conventional fund — not Shariah-screened. May hold stocks "
                          "of conventional banks and other haram businesses. "
                          "Prefer MCB Islamic Fund instead.",
        },
        {
            "name":       "National Savings Regular Income Certificates",
            "type":       "Government",
            "verdict":    "HARAM",
            "confidence": "HIGH",
            "reason":     "Pays a fixed predetermined interest rate which is Riba. "
                          "Not permissible under Islamic law. Use government Ijarah Sukuk instead.",
        },
    ]
