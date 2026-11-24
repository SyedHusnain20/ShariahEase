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

Path step 5a adds two more kinds of card, for entries that already exist ("change that 3000 to 3500", "delete the last entry"):

  * update / delete   the payload lists up to MAX_CANDIDATES entries the person may mean (id, version and what they looked like when
              the card was made). With one candidate it is already chosen; with several the person must pick one by number
              (`pick`): the assistant never guesses between entries. `confirm` acts only on the chosen entry and only if that entry
              is still at the version the card showed (otherwise CardError "stale" and nothing changes). `undo` puts an updated entry
              back to what the card showed (if nobody changed it since) or restores a deleted one.

Everything else (validation of the expense itself, the audit log) is done by the one service layer, expense_service.
"""
import uuid
from datetime import date, datetime, timedelta
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, model_validator
from sqlalchemy import delete, select, update

from app.database.models import MAX_AMOUNT_PAISA, PendingAction
from app.services import expense_service as es

PENDING_TTL = timedelta(minutes=15)
UNDO_WINDOW = timedelta(hours=1)
KEEP_DAYS = 7
MAX_ITEMS = 10
MAX_NOTE_CHARS = 200
MAX_ALTERNATIVES = 5
MAX_CANDIDATES = 5
CARD_NAMESPACE = uuid.UUID("5d1a0b6e-8c43-4f0e-9a57-3b2f6f0c9d11")     # fixed: expense ids are derived from card ids

Language = Literal["en", "ur", "roman_ur"]


class CardError(Exception):
    """`code` is stable: not_found, already_used, cancelled, expired, not_confirmed, undo_window_over, bad_choice, conflict,
    not_chosen (an update/delete card with several candidates: pick one first), stale (the entry changed or is gone since the card)."""

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


class EditTarget(BaseModel):
    """One existing entry as it looked when the card was made. Never the model's words: values read from the database."""
    model_config = ConfigDict(extra="forbid")

    expense_id: StrictInt = Field(ge=1)
    version: StrictInt = Field(ge=1)
    amount_paisa: StrictInt = Field(ge=1, le=MAX_AMOUNT_PAISA)
    category_slug: str                                    # the specific slug when there is one, else the group
    occurred_on: date
    note: Optional[str] = Field(default=None, max_length=MAX_NOTE_CHARS)
    payment_method: es.PaymentMethod = "cash"


class EditChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_amount_paisa: Optional[StrictInt] = Field(default=None, ge=1, le=MAX_AMOUNT_PAISA)
    new_category_slug: Optional[str] = None


class EditPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    v: int = 1
    lang: Language = "en"
    action: Literal["update", "delete"]
    candidates: list[EditTarget] = Field(min_length=1, max_length=MAX_CANDIDATES)
    chosen: Optional[StrictInt] = None                    # index into candidates; None = the person still has to pick
    changes: Optional[EditChanges] = None

    @model_validator(mode="after")
    def _consistent(self):
        if self.chosen is not None and not 0 <= self.chosen < len(self.candidates):
            raise ValueError("chosen is out of range")
        has = self.changes is not None and (self.changes.new_amount_paisa is not None or self.changes.new_category_slug is not None)
        if self.action == "update" and not has:
            raise ValueError("an update needs a change")
        if self.action == "delete" and self.changes is not None:
            raise ValueError("a delete has no changes")
        return self


def payload_of(row: PendingAction):
    """The validated payload of a card, whatever its kind."""
    model = CardPayload if row.kind == "create" else EditPayload
    return model.model_validate(row.payload)


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
    if row.kind != "create":
        return _render_edit(row, rows)
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


def _render_edit(row: PendingAction, rows: list) -> dict:
    """An update/delete card. `items` are the candidates (same shape as a create card's items); `chosen` is None until picked."""
    names = {slug: (en, ur) for slug, en, ur in rows}
    payload = EditPayload.model_validate(row.payload)

    def named(slug):
        return names.get(slug, (slug, slug))

    items = []
    for index, item in enumerate(payload.candidates):
        en, ur = named(item.category_slug)
        items.append({"index": index, "amount_paisa": item.amount_paisa, "amount_display": format_rupees(item.amount_paisa),
                      "category_slug": item.category_slug, "category_name_en": en, "category_name_ur": ur,
                      "occurred_on": item.occurred_on.isoformat(), "note": item.note, "payment_method": item.payment_method,
                      "alternatives": []})
    changes = None
    if payload.changes is not None:
        changes = {"amount_paisa": payload.changes.new_amount_paisa,
                   "amount_display": None if payload.changes.new_amount_paisa is None else format_rupees(payload.changes.new_amount_paisa),
                   "category_slug": payload.changes.new_category_slug,
                   "category_name_en": None, "category_name_ur": None}
        if payload.changes.new_category_slug is not None:
            changes["category_name_en"], changes["category_name_ur"] = named(payload.changes.new_category_slug)
    status = row.status
    if status == "pending" and _aware(row.expires_at) <= es._utcnow():
        status = "expired"
    return {"id": str(row.id), "kind": row.kind, "status": status, "lang": payload.lang,
            "expires_at": _aware(row.expires_at).isoformat(), "items": items,
            "needs_choice": payload.chosen is None, "chosen": payload.chosen, "changes": changes}


# ── creating ──────────────────────────────────────────────────────────────────────────────────────

def create_card(db, user_id: int, *, client_msg_id: str, payload, now: Optional[datetime] = None):
    """(row, created). The same client_msg_id twice gives back the first card and created=False. Commits.
    `payload` is a CardPayload (kind create) or an EditPayload (kind update or delete: the payload's own action)."""
    es._check_user_id(user_id)
    now = now or es._utcnow()
    try:
        db.execute(delete(PendingAction).where(PendingAction.user_id == user_id,
                                               PendingAction.created_at < now - timedelta(days=KEEP_DAYS))
                   .execution_options(synchronize_session=False))
        kind = "create" if isinstance(payload, CardPayload) else payload.action
        statement = (_insert(db)(PendingAction.__table__)
                     .values(id=uuid.uuid4(), user_id=user_id, kind=kind, status="pending", client_msg_id=client_msg_id,
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
    if row.kind != "create":
        return _confirm_edit(db, user_id, row, now)
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


def _current(db, user_id: int, expense_id: int):
    page = es.get_expenses(db, user_id, expense_ids=[expense_id], limit=1)
    return page.items[0] if page.items else None


def _close_stale(db, user_id: int, card_id, now: datetime) -> None:
    """The entry is not as the card showed it: the card can never be used again, so it is closed now (a later bare "yes" must not
    land on it). The entry itself is not touched. Best effort: a failure here still leaves the refusal in place."""
    try:
        db.execute(update(PendingAction)
                   .where(PendingAction.id == card_id, PendingAction.user_id == user_id, PendingAction.status == "pending")
                   .values(status="cancelled", resolved_at=now).execution_options(synchronize_session=False))
        db.commit()
    except Exception:
        db.rollback()


def _confirm_edit(db, user_id: int, row: PendingAction, now: datetime) -> list:
    """Change or delete the ONE entry the person chose, if it is still at the version the card showed. Single use."""
    if EditPayload.model_validate(row.payload).chosen is None:
        raise CardError("not_chosen")
    try:
        claimed = db.execute(
            update(PendingAction)
            .where(
                PendingAction.id == row.id,
                PendingAction.user_id == user_id,
                PendingAction.status == "pending",
                PendingAction.expires_at > now,
            )
            .values(status="confirmed", resolved_at=now)
            .execution_options(synchronize_session=False)
            .returning(PendingAction.payload)
        ).first()
        if claimed is None:
            db.rollback()
            raise _why_not(db, user_id, row.id, now)
        payload = EditPayload.model_validate(claimed[0])
        if payload.chosen is None:                      # never act without a target the person named
            db.rollback()
            raise CardError("not_chosen")
        target = payload.candidates[payload.chosen]
        current = _current(db, user_id, target.expense_id)
        if current is None or current.version != target.version:
            db.rollback()
            _close_stale(db, user_id, row.id, now)
            raise CardError("stale")
        if payload.action == "delete":
            es.delete_expenses(db, user_id, [target.expense_id], actor="ai", commit=False)
            result, saved = {"action": "delete", "expense_ids": [target.expense_id]}, []
        else:
            changes = {}
            if payload.changes.new_amount_paisa is not None:
                changes["amount_paisa"] = payload.changes.new_amount_paisa
            if payload.changes.new_category_slug is not None:
                changes.update(_category_fields(payload.changes.new_category_slug))
            try:
                out = es.update_expense(db, user_id, target.expense_id, target.version, changes, actor="ai", commit=False)
            except (es.VersionConflict, es.NotFound):
                db.rollback()
                _close_stale(db, user_id, row.id, now)
                raise CardError("stale")
            result, saved = {"action": "update", "expense_ids": [target.expense_id], "version_after": out.version}, [out]
        db.execute(update(PendingAction).where(PendingAction.id == row.id, PendingAction.user_id == user_id)
                   .values(result=result).execution_options(synchronize_session=False))
        db.commit()
    except CardError:
        raise
    except Exception:
        db.rollback()
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
        if row.kind != "create":
            raise CardError("bad_choice")
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


def pick(db, user_id: int, card_id, index) -> PendingAction:
    """On an update/delete card: choose which candidate entry is meant (the person may change their mind until confirming)."""
    es._check_user_id(user_id)
    now = es._utcnow()
    for _ in range(3):
        row = get_card(db, user_id, card_id)
        if row is None:
            raise CardError("not_found")
        if row.status != "pending" or _aware(row.expires_at) <= now:
            raise _why_not(db, user_id, row.id, now)
        if row.kind == "create":
            raise CardError("bad_choice")
        payload = EditPayload.model_validate(row.payload)
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(payload.candidates):
            raise CardError("bad_choice")
        payload.chosen = index
        updated = db.execute(update(PendingAction)
                             .where(
                                 PendingAction.id == row.id,
                                 PendingAction.user_id == user_id,
                                 PendingAction.status == "pending",
                                 PendingAction.expires_at > now,
                                 PendingAction.rev == row.rev,
                             )
                             .values(payload=payload.model_dump(mode="json"), rev=row.rev + 1)
                             .execution_options(synchronize_session=False).returning(PendingAction.id)).first()
        if updated is not None:
            db.commit()
            db.refresh(row)
            return row
        db.rollback()
    raise CardError("conflict")


def undo(db, user_id: int, card_id) -> int:
    """Take back what a confirmed card did, once, within UNDO_WINDOW: created entries are deleted (softly), a deleted entry is
    restored, an updated entry goes back to what the card showed (refused as "stale" if somebody changed it since).
    Returns how many entries were touched."""
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
        result = claimed[0] or {}
        ids = result.get("expense_ids", [])
        if row.kind == "delete":
            changed = es.restore_expenses(db, user_id, ids, actor="user", commit=False).changed if ids else []
        elif row.kind == "update":
            payload = EditPayload.model_validate(row.payload)
            target = payload.candidates[payload.chosen]
            back = {}
            if payload.changes.new_amount_paisa is not None:
                back["amount_paisa"] = target.amount_paisa
            if payload.changes.new_category_slug is not None:
                back.update(_category_fields(target.category_slug))
            try:
                es.update_expense(db, user_id, ids[0], result["version_after"], back, actor="user", commit=False)
            except (es.VersionConflict, es.NotFound):
                db.rollback()
                raise CardError("stale")
            changed = [ids[0]]
        else:
            changed = es.delete_expenses(db, user_id, ids, actor="user", commit=False).changed if ids else []
        db.commit()
    except CardError:
        raise
    except Exception:
        db.rollback()
        raise
    return len(changed)
