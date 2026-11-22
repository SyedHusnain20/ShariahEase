"""
Confirmation cards for the AI expense assistant (Path step 4b).

The assistant never saves an expense by itself. It reads a message, shows a CARD ("Rs 500, Tea & snacks, today: save?"),
and only the person's "yes" writes it. A card is a row in `pending_actions`:

  * payload   only validated, normalised values (integer paisa, a real date, a real category slug): never raw model text
  * life      15 minutes. After that it cannot be confirmed. Rows older than 7 days are removed (per user, on each new card).
  * single use  confirm / cancel / undo each claim the card with ONE conditional UPDATE ... WHERE status = 'pending' AND
              user_id = :me AND expires_at > :now ... RETURNING. Two taps, or two parallel requests, can win only once.
  * retry safe  UNIQUE (user_id, client_msg_id): the same chat message sent twice gives back the same card.
              The expense ids written on confirm come from the card id, so even a replayed confirm cannot add twice.
  * ownership  every query filters on user_id taken from the session. Another person's card is "not found", never "forbidden".
  * choose    before confirming, the person can switch an item to a nearby category the assistant offered (never any other).
  * undo      within an hour after confirming, the expenses it wrote are deleted again (soft delete: restorable for 30 days).

Everything else (validation of the expense itself, the audit log) is done by the one service layer, expense_service.
"""
import uuid
from datetime import date, datetime, timedelta
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError
from sqlalchemy import delete, select, update

from app.database.models import MAX_AMOUNT_PAISA, PendingAction
from app.services import expense_service as es

PENDING_TTL = timedelta(minutes=15)
UNDO_WINDOW = timedelta(hours=1)
KEEP_DAYS = 7
MAX_ITEMS = 10
MAX_NOTE_CHARS = 200
MAX_ALTERNATIVES = 5
CARD_NAMESPACE = uuid.UUID("5d1a0b6e-8c43-4f0e-9a57-3b2f6f0c9d11")     # fixed: expense ids are derived from card ids

Language = Literal["en", "ur", "roman_ur"]


class CardError(Exception):
    """`code` is stable: not_found, already_used, cancelled, expired, not_confirmed, undo_window_over, bad_choice, conflict."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


# ── what a card holds ─────────────────────────────────────────────────────────────────────────────

class CardItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount_paisa: StrictInt = Field(ge=1, le=MAX_AMOUNT_PAISA)
    category_slug: str
    occurred_on: date
    note: Optional[str] = Field(default=None, max_length=MAX_NOTE_CHARS)
    payment_method: es.PaymentMethod = "cash"
    alternatives: list[str] = Field(default_factory=list, max_length=MAX_ALTERNATIVES + 1)


class CardPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    v: int = 1
    lang: Language = "en"
    items: list[CardItem] = Field(min_length=1, max_length=MAX_ITEMS)


def format_rupees(paisa: int) -> str:
    whole, part = divmod(int(paisa), 100)
    return f"Rs {whole:,}" if part == 0 else f"Rs {whole:,}.{part:02d}"


def alternatives_for(slug: str, rows: list) -> list:
    """The category the assistant chose, its neighbours in the same group, and "other": what the person may switch to."""
    group = slug.split(".")[0]
    same = [r[0] for r in rows if r[0] != slug and "." in r[0] and r[0].split(".")[0] == group]
    out = [slug] + same[:MAX_ALTERNATIVES - 2]
    if "other.other" in {r[0] for r in rows} and "other.other" not in out:
        out.append("other.other")
    return out[:MAX_ALTERNATIVES]


def _category_fields(slug: str) -> dict:
    """The service takes a whole group ('food') and a specific one ('food.groceries') in different fields."""
    return {"subcategory_slug": slug} if "." in slug else {"category_slug": slug}


def derived_client_id(card_id: uuid.UUID, index: int) -> uuid.UUID:
    return uuid.uuid5(CARD_NAMESPACE, f"{card_id}:{index}")


# ── reading ───────────────────────────────────────────────────────────────────────────────────────

def _insert(db):
    name = db.get_bind().dialect.name
    if name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise RuntimeError(f"unsupported database: {name}")
    return insert


def _aware(value: datetime) -> datetime:
    return es._aware(value)


def get_card(db, user_id: int, card_id) -> Optional[PendingAction]:
    es._check_user_id(user_id)
    try:
        card_id = card_id if isinstance(card_id, uuid.UUID) else uuid.UUID(str(card_id))
    except ValueError:
        return None
    return db.execute(select(PendingAction).where(PendingAction.id == card_id, PendingAction.user_id == user_id)).scalar_one_or_none()


def get_by_message(db, user_id: int, client_msg_id: str) -> Optional[PendingAction]:
    es._check_user_id(user_id)
    return db.execute(select(PendingAction).where(PendingAction.user_id == user_id,
                                                  PendingAction.client_msg_id == client_msg_id)).scalar_one_or_none()


def latest_pending(db, user_id: int, now: Optional[datetime] = None) -> Optional[PendingAction]:
    """The newest card that can still be confirmed (used for a plain "yes" typed in the chat)."""
    es._check_user_id(user_id)
    now = now or es._utcnow()
    return db.execute(select(PendingAction).where(PendingAction.user_id == user_id, PendingAction.status == "pending",
                                                  PendingAction.expires_at > now)
                      .order_by(PendingAction.created_at.desc()).limit(1)).scalar_one_or_none()


def render(row: PendingAction, rows: list) -> dict:
    """The card as the app shows it. `rows` is the category list [(slug, name_en, name_ur)]."""
    names = {slug: (en, ur) for slug, en, ur in rows}
    payload = CardPayload.model_validate(row.payload)
    items = []
    for index, item in enumerate(payload.items):
        en, ur = names.get(item.category_slug, (item.category_slug, item.category_slug))
        items.append({
            "index": index,
            "amount_paisa": item.amount_paisa,
            "amount_display": format_rupees(item.amount_paisa),
            "category_slug": item.category_slug, "category_name_en": en, "category_name_ur": ur,
            "occurred_on": item.occurred_on.isoformat(),
            "note": item.note,
            "payment_method": item.payment_method,
            "alternatives": [{"slug": s, "name_en": names.get(s, (s, s))[0], "name_ur": names.get(s, (s, s))[1]}
                             for s in item.alternatives if s != item.category_slug],
        })
    status = row.status
    if status == "pending" and _aware(row.expires_at) <= es._utcnow():
        status = "expired"
    return {"id": str(row.id), "kind": row.kind, "status": status, "lang": payload.lang,
            "expires_at": _aware(row.expires_at).isoformat(), "items": items}


# ── creating ──────────────────────────────────────────────────────────────────────────────────────

def create_card(db, user_id: int, *, client_msg_id: str, payload: CardPayload, now: Optional[datetime] = None):
    """(row, created). The same client_msg_id twice gives back the first card and created=False. Commits."""
    es._check_user_id(user_id)
    now = now or es._utcnow()
    try:
        db.execute(delete(PendingAction).where(PendingAction.user_id == user_id,
                                               PendingAction.created_at < now - timedelta(days=KEEP_DAYS))
                   .execution_options(synchronize_session=False))
        statement = (_insert(db)(PendingAction.__table__)
                     .values(id=uuid.uuid4(), user_id=user_id, kind="create", status="pending", client_msg_id=client_msg_id,
                             payload=payload.model_dump(mode="json"), rev=0, created_at=now, expires_at=now + PENDING_TTL)
                     .on_conflict_do_nothing(index_elements=["user_id", "client_msg_id"])
                     .returning(PendingAction.__table__.c.id))
        created = db.execute(statement).scalar() is not None
        db.commit()
    except Exception:
        db.rollback()
        raise
    return get_by_message(db, user_id, client_msg_id), created


# ── resolving ─────────────────────────────────────────────────────────────────────────────────────

def _why_not(db, user_id: int, card_id, now: datetime) -> CardError:
    """Why a claim of a pending card found nothing. Also marks a lapsed card as expired."""
    row = get_card(db, user_id, card_id)
    if row is None:
        return CardError("not_found")
    if row.status == "confirmed":
        return CardError("already_used")
    if row.status == "cancelled":
        return CardError("cancelled")
    if row.status == "pending":
        db.execute(update(PendingAction).where(PendingAction.id == row.id, PendingAction.user_id == user_id,
                                               PendingAction.status == "pending", PendingAction.expires_at <= now)
                   .values(status="expired").execution_options(synchronize_session=False))
        db.commit()
    return CardError("expired")


def confirm(db, user_id: int, card_id) -> list:
    """Write the card's expenses (once). Returns the saved ExpenseOut list. Raises CardError or the service's errors."""
    es._check_user_id(user_id)
    now = es._utcnow()
    row = get_card(db, user_id, card_id)
    if row is None:
        raise CardError("not_found")
    try:
        claimed = db.execute(
            update(PendingAction)
            .where(PendingAction.id == row.id, PendingAction.user_id == user_id, PendingAction.status == "pending",
                   PendingAction.expires_at > now)
            .values(status="confirmed", resolved_at=now)
            .execution_options(synchronize_session=False)
            .returning(PendingAction.payload)
        ).first()
        if claimed is None:
            db.rollback()
            raise _why_not(db, user_id, row.id, now)
        payload = CardPayload.model_validate(claimed[0])
        items = [{"client_id": derived_client_id(row.id, i), "amount_paisa": item.amount_paisa, "occurred_on": item.occurred_on,
                  "note": item.note, "payment_method": item.payment_method, **_category_fields(item.category_slug)}
                 for i, item in enumerate(payload.items)]
        results = es.add_expenses(db, user_id, items, source="ai_chat", commit=False)
        saved = [r.expense for r in results if r.expense is not None]
        db.execute(update(PendingAction).where(PendingAction.id == row.id, PendingAction.user_id == user_id)
                   .values(result={"expense_ids": [e.id for e in saved]}).execution_options(synchronize_session=False))
        db.commit()
    except CardError:
        raise
    except Exception:
        db.rollback()               # the claim is rolled back too: the card can be confirmed again after a fix, or cancelled
        raise
    return saved


def cancel(db, user_id: int, card_id) -> None:
    es._check_user_id(user_id)
    now = es._utcnow()
    row = get_card(db, user_id, card_id)
    if row is None:
        raise CardError("not_found")
    claimed = db.execute(update(PendingAction)
                         .where(PendingAction.id == row.id, PendingAction.user_id == user_id, PendingAction.status == "pending",
                                PendingAction.expires_at > now)
                         .values(status="cancelled", resolved_at=now).execution_options(synchronize_session=False)
                         .returning(PendingAction.id)).first()
    if claimed is None:
        db.rollback()
        raise _why_not(db, user_id, row.id, now)
    db.commit()


def choose(db, user_id: int, card_id, item_index, category_slug) -> PendingAction:
    """Switch one item to a category the assistant offered for it. Any other slug is refused and nothing changes."""
    es._check_user_id(user_id)
    now = es._utcnow()
    for _ in range(3):                                               # a parallel choose may move `rev`: read again
        row = get_card(db, user_id, card_id)
        if row is None:
            raise CardError("not_found")
        if row.status != "pending" or _aware(row.expires_at) <= now:
            raise _why_not(db, user_id, row.id, now)
        payload = CardPayload.model_validate(row.payload)
        if isinstance(item_index, bool) or not isinstance(item_index, int) or not 0 <= item_index < len(payload.items):
            raise CardError("bad_choice")
        item = payload.items[item_index]
        if category_slug not in item.alternatives:
            raise CardError("bad_choice")
        item.category_slug = category_slug
        updated = db.execute(update(PendingAction)
                             .where(PendingAction.id == row.id, PendingAction.user_id == user_id, PendingAction.status == "pending",
                                    PendingAction.expires_at > now, PendingAction.rev == row.rev)
                             .values(payload=payload.model_dump(mode="json"), rev=row.rev + 1)
                             .execution_options(synchronize_session=False).returning(PendingAction.id)).first()
        if updated is not None:
            db.commit()
            db.refresh(row)
            return row
        db.rollback()
    raise CardError("conflict")


def undo(db, user_id: int, card_id) -> int:
    """Delete (softly) what a confirmed card wrote, once, within UNDO_WINDOW. Returns how many expenses were removed."""
    es._check_user_id(user_id)
    now = es._utcnow()
    row = get_card(db, user_id, card_id)
    if row is None:
        raise CardError("not_found")
    try:
        claimed = db.execute(
            update(PendingAction)
            .where(PendingAction.id == row.id, PendingAction.user_id == user_id, PendingAction.status == "confirmed",
                   PendingAction.undone_at.is_(None), PendingAction.resolved_at >= now - UNDO_WINDOW)
            .values(undone_at=now).execution_options(synchronize_session=False).returning(PendingAction.result)
        ).first()
        if claimed is None:
            db.rollback()
            if row.status != "confirmed":
                raise CardError("not_confirmed")
            raise CardError("already_used" if row.undone_at is not None else "undo_window_over")
        ids = (claimed[0] or {}).get("expense_ids", [])
        changed = es.delete_expenses(db, user_id, ids, actor="user", commit=False).changed if ids else []
        db.commit()
    except CardError:
        raise
    except Exception:
        db.rollback()
        raise
    return len(changed)
