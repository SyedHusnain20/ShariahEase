"""
REST API for the expense tracker (Phase 1b).

    GET    /api/expenses/categories
    GET    /api/expenses            ?month=YYYY-MM | ?date_from&date_to  &category &limit &before_date &before_id
    GET    /api/expenses/summary    ?month=YYYY-MM | ?date_from&date_to  &group_by
    POST   /api/expenses
    PATCH  /api/expenses/{id}       body carries the `version` the client last saw
    DELETE /api/expenses/{id}       soft delete
    POST   /api/expenses/{id}/restore   the "undo" for a delete

Rules this file enforces (the service layer enforces the rest):
  * every route needs a logged-in user (401 otherwise); `user_id` always comes from the session, never from a body
  * another user's row is 404, never 403, so ids cannot be probed
  * a stale edit is 409 with the current version
  * writes are limited per user (429 + Retry-After), and need an `X-Requested-With` header so a forged
    cross-site form post cannot ride on the login cookie
  * money arrives as rupees text/whole rupees and is turned into integer paisa by services/money.py
"""
import re
import uuid
from datetime import date
from typing import Optional, Union

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr
from sqlalchemy.orm import Session

from app.database import models
from app.database.db import get_db
from app.dependencies import get_current_user
from app.services import expense_service as svc
from app.services.money import MoneyError, parse_rupees_to_paisa
from app.services.rate_limit import SlidingWindowLimiter

router = APIRouter(prefix="/api/expenses", tags=["expenses"])

WRITES_PER_MINUTE = 60
write_limiter = SlidingWindowLimiter(WRITES_PER_MINUTE, 60)

REQUIRED_HEADER_VALUE = "ShariahEase"
_MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$", re.ASCII)


# ── dependencies ──────────────────────────────────────────────────────────────

def current_user(user: models.User = Depends(get_current_user)) -> models.User:
    """Login required. (Wrapped in one place so every route below visibly depends on it.)"""
    return user


def write_user(
    user: models.User = Depends(current_user),
    x_requested_with: Optional[str] = Header(default=None),
) -> models.User:
    """Login + anti-forgery header + per-user write limit, for every route that changes data."""
    if x_requested_with != REQUIRED_HEADER_VALUE:
        raise HTTPException(status_code=403, detail={"code": "missing_header"})
    wait = write_limiter.check(user.id)
    if wait > 0:
        raise HTTPException(status_code=429, detail={"code": "rate_limited", "retry_after": int(wait) + 1},
                            headers={"Retry-After": str(int(wait) + 1)})
    return user


# ── request bodies (no user_id anywhere; unknown fields are refused) ───────────

Amount = Union[StrictStr, StrictInt]


class ExpenseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: uuid.UUID
    amount: Amount                       # rupees, e.g. "1,250.50" or 500
    occurred_on: date
    category_slug: Optional[str] = None
    subcategory_slug: Optional[str] = None
    note: Optional[str] = None
    payment_method: str = "cash"


class ExpensePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: StrictInt                   # the version the client last read
    amount: Optional[Amount] = None
    occurred_on: Optional[date] = None
    category_slug: Optional[str] = None
    subcategory_slug: Optional[str] = None
    note: Optional[str] = None           # null clears the note
    payment_method: Optional[str] = None


# ── helpers ────────────────────────────────────────────────────────────────────

def _fail(exc: Exception) -> HTTPException:
    if isinstance(exc, svc.InvalidInput):
        return HTTPException(status_code=422, detail={"code": exc.code, "errors": exc.errors})
    if isinstance(exc, svc.NotFound):
        return HTTPException(status_code=404, detail={"code": exc.code})
    if isinstance(exc, svc.VersionConflict):
        return HTTPException(status_code=409, detail={"code": exc.code, "current_version": exc.current_version})
    if isinstance(exc, MoneyError):
        return HTTPException(status_code=422, detail={"code": "invalid_input",
                                                      "errors": [{"field": "amount", "problem": exc.code}]})
    raise exc


def _paisa(amount) -> int:
    return parse_rupees_to_paisa(amount)


def _window(month: Optional[str], date_from: Optional[date], date_to: Optional[date]):
    """`month=2026-10` means [2026-10-01, 2026-11-01). Otherwise an explicit [from, to) window."""
    if month is not None:
        if date_from is not None or date_to is not None:
            raise _fail(svc.InvalidInput([{"field": "month", "problem": "use either month or date_from/date_to"}]))
        m = _MONTH.match(month)
        if not m:
            raise _fail(svc.InvalidInput([{"field": "month", "problem": "expected YYYY-MM"}]))
        year, mon = int(m.group(1)), int(m.group(2))
        if not 2000 <= year <= 2100:
            raise _fail(svc.InvalidInput([{"field": "month", "problem": "year out of range"}]))
        start = date(year, mon, 1)
        end = date(year + 1, 1, 1) if mon == 12 else date(year, mon + 1, 1)
        return start, end
    return date_from, date_to


# ── routes ─────────────────────────────────────────────────────────────────────

@router.get("/categories")
def categories(user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    return [c.model_dump() for c in svc.list_categories(db)]


@router.get("/summary")
def summary(
    month: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    group_by: str = "category",
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
):
    start, end = _window(month, date_from, date_to)
    if start is None or end is None:
        raise _fail(svc.InvalidInput([{"field": "month", "problem": "month or date_from and date_to are required"}]))
    try:
        return svc.summarize(db, user.id, date_from=start, date_to=end, group_by=group_by).model_dump(mode="json")
    except svc.ExpenseError as exc:
        raise _fail(exc)


@router.get("")
def list_expenses(
    month: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    category: Optional[str] = None,
    limit: int = Query(default=svc.DEFAULT_PAGE_SIZE),
    before_date: Optional[date] = None,
    before_id: Optional[int] = None,
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
):
    start, end = _window(month, date_from, date_to)
    if (before_date is None) != (before_id is None):
        raise _fail(svc.InvalidInput([{"field": "before_date", "problem": "send before_date and before_id together"}]))
    before = (before_date, before_id) if before_date is not None else None
    try:
        page = svc.get_expenses(db, user.id, date_from=start, date_to=end, category_slug=category,
                                limit=limit, before=before)
    except svc.ExpenseError as exc:
        raise _fail(exc)
    data = page.model_dump(mode="json")
    nxt = data.pop("next_before")
    data["next_before"] = None if nxt is None else {"date": nxt[0], "id": nxt[1]}
    return data


@router.post("")
def create_expense(body: ExpenseCreate, user: models.User = Depends(write_user), db: Session = Depends(get_db)):
    try:
        item = {
            "client_id": body.client_id, "amount_paisa": _paisa(body.amount), "occurred_on": body.occurred_on,
            "category_slug": body.category_slug, "subcategory_slug": body.subcategory_slug,
            "note": body.note, "payment_method": body.payment_method,
        }
        result = svc.add_expenses(db, user.id, [item], source="manual")[0]
    except (svc.ExpenseError, MoneyError) as exc:
        raise _fail(exc)
    # a retry of the same client_id answers 200 with the original row; a deleted original is not brought back
    if result.already_deleted:
        raise HTTPException(status_code=409, detail={"code": "already_deleted"})
    out = result.model_dump(mode="json")
    out["status"] = "created" if result.created else "duplicate"
    return JSONResponse(out, status_code=201 if result.created else 200)


@router.patch("/{expense_id}")
def update_expense(expense_id: int, body: ExpensePatch, user: models.User = Depends(write_user),
                   db: Session = Depends(get_db)):
    given = body.model_fields_set - {"version"}
    changes: dict = {}
    try:
        for name in given:
            value = getattr(body, name)
            if name == "amount":
                if value is None:
                    raise svc.InvalidInput([{"field": "amount", "problem": "cannot be cleared"}])
                changes["amount_paisa"] = _paisa(value)
            else:
                changes[name] = value
        out = svc.update_expense(db, user.id, expense_id, body.version, changes, actor="user")
    except (svc.ExpenseError, MoneyError) as exc:
        raise _fail(exc)
    return out.model_dump(mode="json")


@router.delete("/{expense_id}")
def delete_expense(expense_id: int, user: models.User = Depends(write_user), db: Session = Depends(get_db)):
    try:
        res = svc.delete_expenses(db, user.id, [expense_id])
    except svc.ExpenseError as exc:
        raise _fail(exc)
    if not res.changed:
        raise _fail(svc.NotFound("expense not found"))
    return res.model_dump()


@router.post("/{expense_id}/restore")
def restore_expense(expense_id: int, user: models.User = Depends(write_user), db: Session = Depends(get_db)):
    try:
        res = svc.restore_expenses(db, user.id, [expense_id])
    except svc.ExpenseError as exc:
        raise _fail(exc)
    if not res.changed:
        raise _fail(svc.NotFound("expense not found"))
    page = svc.get_expenses(db, user.id, expense_ids=[expense_id])
    return {"expense": page.items[0].model_dump(mode="json") if page.items else None}
