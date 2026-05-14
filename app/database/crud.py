from sqlalchemy.orm import Session
from app.database import models
from app.models import schemas


# ── ZAKAT RECORDS ─────────────────────────────────────────

def save_zakat_record(db: Session, request: schemas.ZakatRequest, result: schemas.ZakatResult, user_id: int = None):
    """Save a completed Zakat calculation to the database."""
    record = models.ZakatRecord(
        user_id             = user_id,
        user_name           = request.user_name,
        cash                = request.cash,
        gold_value          = request.gold_value,
        silver_value        = request.silver_value,
        business_inventory  = request.business_inventory,
        receivables         = request.receivables,
        debts               = request.debts,
        immediate_expenses  = request.immediate_expenses,
        total_assets        = result.total_assets,
        total_deductions    = result.total_deductions,
        zakatable_wealth    = result.zakatable_wealth,
        nisab_threshold     = result.nisab_threshold,
        zakat_due           = result.zakat_due,
        is_zakat_due        = result.is_zakat_due,
        nisab_rate_used     = request.nisab_rate_used,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def get_zakat_history(db: Session, user_id: int, limit: int = 10):
    """Get the most recent Zakat calculations for a specific user."""
    return (
        db.query(models.ZakatRecord)
        .filter(models.ZakatRecord.user_id == user_id)
        .order_by(models.ZakatRecord.calculated_at.desc())
        .limit(limit)
        .all()
    )


# ── SCREENER HISTORY ──────────────────────────────────────

def save_screener_result(db: Session, query: str, verdict: str, explanation: str, user_id: int = None):
    """Save a halal screening result."""
    record = models.ScreenerHistory(
        user_id     = user_id,
        query       = query,
        verdict     = verdict,
        explanation = explanation,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def get_screener_history(db: Session, user_id: int, limit: int = 10):
    """Get the most recent screening queries for a specific user."""
    return (
        db.query(models.ScreenerHistory)
        .filter(models.ScreenerHistory.user_id == user_id)
        .order_by(models.ScreenerHistory.screened_at.desc())
        .limit(limit)
        .all()
    )


# ── CHAT MESSAGES ─────────────────────────────────────────

def save_message(db: Session, session_id: str, role: str, content: str, language: str = "en", user_id: int = None):
    """Save a single chat message."""
    msg = models.ChatMessage(
        user_id    = user_id,
        session_id = session_id,
        role       = role,
        content    = content,
        language   = language,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


def get_chat_history(db: Session, session_id: str, limit: int = 20):
    """Get conversation history for a session — oldest first."""
    return (
        db.query(models.ChatMessage)
        .filter(models.ChatMessage.session_id == session_id)
        .order_by(models.ChatMessage.created_at.asc())
        .limit(limit)
        .all()
    )