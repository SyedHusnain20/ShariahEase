"""
REST API for the AI expense assistant's confirmation cards (Path step 4b). The chat message itself goes to
POST /chat/message (see app/routers/chatbot.py); these routes act on the card that came back.

    GET  /api/expense-ai/status           is the assistant on, how much of this month is used, is the daily breaker open
    GET  /api/expense-ai/pending          the newest card that can still be confirmed (or {"card": null})
    POST /api/expense-ai/{id}/confirm     save what the card shows (once)
    POST /api/expense-ai/{id}/cancel      throw the card away
    POST /api/expense-ai/{id}/choose      body {"item": 0, "category_slug": "..."}: switch one item to an offered category
    POST /api/expense-ai/{id}/undo        take back what a confirmed card saved (within an hour; soft delete)

Same rules as the expense routes: login required (401), writes need the X-Requested-With header and are rate limited,
another person's card is 404 (never 403), user_id comes only from the session. 409 = already used / cancelled / expired,
422 = a refused choice or a refused expense.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr
from sqlalchemy.orm import Session

from app.database import models
from app.database.db import get_db
from app.routers.expenses import current_user, write_user
from app.services import expense_ai, expense_service as es, pending_actions as pa
from app.services.expense_understand import taxonomy

router = APIRouter(prefix="/api/expense-ai", tags=["expense-ai"])

_STATUS = {"not_found": 404, "already_used": 409, "cancelled": 409, "expired": 409, "not_confirmed": 409,
           "undo_window_over": 409, "bad_choice": 422, "conflict": 409}


class ChooseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item: StrictInt
    category_slug: StrictStr


def _card_error(err: pa.CardError) -> HTTPException:
    return HTTPException(status_code=_STATUS.get(err.code, 409), detail={"code": err.code})


@router.get("/status")
def status(db: Session = Depends(get_db), user: models.User = Depends(current_user)):
    return expense_ai.usage_status(db, user.id)


@router.get("/pending")
def pending(db: Session = Depends(get_db), user: models.User = Depends(current_user)):
    row = pa.latest_pending(db, user.id)
    return {"card": pa.render(row, taxonomy(db)) if row is not None else None}


@router.post("/{card_id}/confirm")
def confirm(card_id: str, db: Session = Depends(get_db), user: models.User = Depends(write_user)):
    try:
        saved = pa.confirm(db, user.id, card_id)
    except pa.CardError as err:
        raise _card_error(err)
    except es.InvalidInput as err:
        raise HTTPException(status_code=422, detail={"code": "invalid_input", "errors": err.errors})
    return {"status": "confirmed", "expenses": [e.model_dump(mode="json") for e in saved]}


@router.post("/{card_id}/cancel")
def cancel(card_id: str, db: Session = Depends(get_db), user: models.User = Depends(write_user)):
    try:
        pa.cancel(db, user.id, card_id)
    except pa.CardError as err:
        raise _card_error(err)
    return {"status": "cancelled"}


@router.post("/{card_id}/choose")
def choose(card_id: str, body: ChooseBody, db: Session = Depends(get_db), user: models.User = Depends(write_user)):
    try:
        row = pa.choose(db, user.id, card_id, body.item, body.category_slug)
    except pa.CardError as err:
        raise _card_error(err)
    return {"status": "pending", "card": pa.render(row, taxonomy(db))}


@router.post("/{card_id}/undo")
def undo(card_id: str, db: Session = Depends(get_db), user: models.User = Depends(write_user)):
    try:
        removed = pa.undo(db, user.id, card_id)
    except pa.CardError as err:
        raise _card_error(err)
    return {"status": "undone", "removed": removed}
