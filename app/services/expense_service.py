"""
Expense service layer: the single source of truth for reading and writing expenses.

REST routes, the chatbot, WhatsApp and any future app all call these seven functions:

    add_expenses, update_expense, delete_expenses, restore_expenses,
    get_expenses, summarize, list_categories

Two more functions serve the data-rights and housekeeping needs (Path step 2):

    export_expenses            every row the user still has in the database, for the data export
    purge_deleted_expenses     (system) really deletes rows that were soft-deleted more than 30 days ago
    run_maintenance            (system) runs the purge at most once a day, started by normal traffic

Rules every function follows
  * `user_id` is the first argument and comes from the authenticated session, never from a request
    body, model output or message text. No input schema below has a `user_id` field (extra fields are
    rejected, so a body that contains one is refused).
  * Every query on `expenses` filters on `user_id`. Reads also filter `deleted_at IS NULL`.
  * Money is integer paisa. All sums are done by SQL.
  * Dates are the user's LOCAL date. Windows are start-inclusive, end-exclusive: [date_from, date_to).
  * The audit log stores ids, field NAMES and counts only: never note text and never amounts.
  * Errors never echo user text (a note may be personal), only field names and error kinds.
  * Functions commit when they finish. Pass commit=False to compose several calls in one transaction.
"""
from __future__ import annotations

import logging
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Literal, Optional, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, field_validator
from sqlalchemy import BigInteger, Integer, and_, cast, delete, func, or_, select, update

from app.database.models import (
    AuditLog, Category, Expense, MaintenanceRun, Subcategory, User, MAX_AMOUNT_PAISA, MAX_NOTE_CHARS,
)

log = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "Asia/Karachi"
MAX_ITEMS_PER_ADD = 20
MAX_IDS_PER_CALL = 100
MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50
MAX_FUTURE_DAYS = 1            # an expense may be dated at most one day ahead of the user's local today
PURGE_AFTER_DAYS = 30          # a soft-deleted expense can be undone for this long, then it is really deleted
MAINTENANCE_INTERVAL = timedelta(hours=24)
MAINTENANCE_PURGE = "purge_deleted_expenses"     # name of the row in maintenance_runs
EXPORT_BATCH = 1000

PaymentMethod = Literal["cash", "bank_transfer", "card", "wallet", "other"]
Source = Literal["manual", "ai_chat", "whatsapp", "pwa_sync"]
Actor = Literal["user", "ai", "whatsapp", "admin", "system"]
GroupBy = Literal["none", "category", "subcategory", "day", "month"]

_ACTOR_FOR_SOURCE = {"manual": "user", "pwa_sync": "user", "ai_chat": "ai", "whatsapp": "whatsapp"}
_ACTORS = frozenset({"user", "ai", "whatsapp", "admin", "system"})
_AUDIT_DETAIL_KEYS = frozenset({"source", "fields", "version", "count"})
_BIDI_CONTROLS = {chr(c) for c in (0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069)}


# ── errors ───────────────────────────────────────────────────────────────────────────────────────

class ExpenseError(Exception):
    """Base class. `code` is a stable machine-readable string for the REST layer."""
    code = "error"


class InvalidInput(ExpenseError):
    code = "invalid_input"

    def __init__(self, errors: list[dict]):
        self.errors = errors                       # [{"field": ..., "problem": ...}]  (never the user's text)
        super().__init__("; ".join(f'{e["field"]}: {e["problem"]}' for e in errors))


class NotFound(ExpenseError):
    code = "not_found"


class VersionConflict(ExpenseError):
    code = "version_conflict"

    def __init__(self, current_version: int):
        self.current_version = current_version
        super().__init__("the expense was changed since it was read")


def _bad(field: str, problem: str) -> InvalidInput:
    return InvalidInput([{"field": field, "problem": problem}])


# ── input and output models ──────────────────────────────────────────────────────────────────────

def clean_note(value):
    """Trim, normalise and strip control and direction-override characters. Empty becomes None."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("must be text")
    text = unicodedata.normalize("NFC", value)
    out = []
    for ch in text:
        if ch in _BIDI_CONTROLS:
            continue
        if ch in "\r\n\t":
            out.append(" ")
        elif unicodedata.category(ch) == "Cc":
            continue
        else:
            out.append(ch)               # keeps ZWNJ/ZWJ, which Urdu and Persian text needs
    text = " ".join("".join(out).split())
    if len(text) > MAX_NOTE_CHARS:
        raise ValueError(f"must be at most {MAX_NOTE_CHARS} characters")
    return text or None


def _parse_local_date(value):
    """A date or an ISO 'YYYY-MM-DD' string. Numbers and datetimes are refused (no silent conversions)."""
    if isinstance(value, datetime):
        raise ValueError("must be a date, not a date and time")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            raise ValueError("must be a date like 2026-10-31") from None
    raise ValueError("must be a date like 2026-10-31")


class ExpenseIn(BaseModel):
    """One expense to create. There is deliberately no user_id here."""
    model_config = ConfigDict(extra="forbid")

    client_id: uuid.UUID                                           # idempotency key chosen by the caller
    amount_paisa: StrictInt = Field(ge=1, le=MAX_AMOUNT_PAISA)
    occurred_on: date
    category_slug: Optional[str] = None
    subcategory_slug: Optional[str] = None
    note: Optional[str] = None
    payment_method: PaymentMethod = "cash"

    @field_validator("occurred_on", mode="before")
    @classmethod
    def check_date(cls, v):
        return _parse_local_date(v)

    @field_validator("note", mode="before")
    @classmethod
    def check_note(cls, v):
        return clean_note(v)


class ExpenseUpdate(BaseModel):
    """Fields to change. Only fields that are present are changed; `note: null` clears the note."""
    model_config = ConfigDict(extra="forbid")

    amount_paisa: Optional[StrictInt] = Field(default=None, ge=1, le=MAX_AMOUNT_PAISA)
    occurred_on: Optional[date] = None
    category_slug: Optional[str] = None
    subcategory_slug: Optional[str] = None
    note: Optional[str] = None
    payment_method: Optional[PaymentMethod] = None

    @field_validator("occurred_on", mode="before")
    @classmethod
    def check_date(cls, v):
        return None if v is None else _parse_local_date(v)

    @field_validator("note", mode="before")
    @classmethod
    def check_note(cls, v):
        return clean_note(v)


class ExpenseOut(BaseModel):
    id: int
    client_id: uuid.UUID
    amount_paisa: int
    occurred_on: date
    category_slug: str
    subcategory_slug: Optional[str]
    note: Optional[str]
    payment_method: str
    source: str
    shariah_flag: Optional[str]
    shariah_flag_dismissed: bool
    version: int
    created_at: datetime
    updated_at: datetime


class AddResult(BaseModel):
    client_id: uuid.UUID
    created: bool                       # False = this client_id was already used (a retry); nothing was written
    already_deleted: bool = False       # the earlier row was deleted since: it is NOT brought back
    expense: Optional[ExpenseOut] = None


class DeleteResult(BaseModel):
    changed: list[int]                  # ids that were deleted (or restored)
    not_found: list[int]                # ids that do not exist for this user, or were already in that state


class ExpensePage(BaseModel):
    items: list[ExpenseOut]
    next_before: Optional[tuple[date, int]] = None      # pass back as `before` to get the next page


class SummaryGroup(BaseModel):
    key: str
    label_en: Optional[str] = None
    label_ur: Optional[str] = None
    total_paisa: int
    count: int


class Summary(BaseModel):
    date_from: date
    date_to: date
    total_paisa: int
    count: int
    group_by: str
    groups: list[SummaryGroup]


class SubcategoryOut(BaseModel):
    slug: str
    name_en: str
    name_ur: str
    charity_type: Optional[str]
    default_flag: Optional[str]


class CategoryOut(BaseModel):
    slug: str
    name_en: str
    name_ur: str
    is_charity: bool
    subcategories: list[SubcategoryOut]


class ExportRow(BaseModel):
    """One row of the user's data export. Includes soft-deleted rows that are not purged yet (deleted_at is set)."""
    id: int
    client_id: uuid.UUID
    occurred_on: date
    amount_paisa: int
    currency: str
    category_slug: str
    category_name_en: str
    subcategory_slug: Optional[str]
    subcategory_name_en: Optional[str]
    note: Optional[str]
    payment_method: str
    source: str
    shariah_flag: Optional[str]
    shariah_flag_dismissed: bool
    version: int
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime]


# ── helpers ──────────────────────────────────────────────────────────────────────────────────────

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    """SQLite returns naive datetimes; they are UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _check_user_id(user_id) -> int:
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 1:
        raise TypeError("user_id must come from the authenticated session as a positive int")
    return user_id


def _coerce(model, value, field: str = "body"):
    """Accept a model instance or a dict. Validation problems become InvalidInput without the values."""
    if isinstance(value, model):
        return value
    try:
        return model.model_validate(value)
    except ValidationError as exc:
        raise InvalidInput([
            {"field": ".".join(str(p) for p in err["loc"]) or field, "problem": err["type"]}
            for err in exc.errors()
        ]) from None


def _check_ids(ids: Iterable) -> list[int]:
    out: list[int] = []
    for value in ids:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise _bad("ids", "must be positive integers")
        if value not in out:
            out.append(value)
    if not out:
        raise _bad("ids", "at least one id is required")
    if len(out) > MAX_IDS_PER_CALL:
        raise _bad("ids", f"at most {MAX_IDS_PER_CALL} ids per call")
    return out


def _check_actor(actor) -> None:
    if actor not in _ACTORS:
        raise _bad("actor", "unknown actor")


def _check_date(name: str, value) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise _bad(name, "must be a date")
    return value


@contextmanager
def _unit(db, commit: bool):
    try:
        yield
        db.commit() if commit else db.flush()
    except Exception:
        if commit:
            db.rollback()
        raise


def _insert_ignore(db):
    """INSERT ... ON CONFLICT (user_id, client_id) DO NOTHING, for PostgreSQL and SQLite."""
    name = db.get_bind().dialect.name
    if name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise RuntimeError(f"unsupported database: {name}")
    return insert(Expense).on_conflict_do_nothing(index_elements=["user_id", "client_id"])


def _audit(db, user_id: int, actor: str, action: str, entity_id: Optional[int] = None, detail: Optional[dict] = None):
    """Ids, field names and counts only. Anything else is a programming error."""
    if detail and not set(detail) <= _AUDIT_DETAIL_KEYS:
        raise ValueError("audit detail may only contain: " + ", ".join(sorted(_AUDIT_DETAIL_KEYS)))
    db.add(AuditLog(user_id=user_id, actor=actor, action=action, entity_type="expense",
                    entity_id=None if entity_id is None else str(entity_id), detail=detail or None))


class _Taxonomy:
    def __init__(self, db):
        self.categories = {c.slug: c for c in db.execute(select(Category)).scalars()}
        self.subs = {s.slug: s for s in db.execute(select(Subcategory)).scalars()}
        self.category_slug_by_id = {c.id: c.slug for c in self.categories.values()}

    def resolve(self, category_slug, subcategory_slug, prefix: str):
        """Returns (category_id, subcategory_id or None, default shariah flag or None)."""
        if category_slug is None and subcategory_slug is None:
            raise _bad(f"{prefix}category_slug", "category_slug or subcategory_slug is required")
        for name, value in (("category_slug", category_slug), ("subcategory_slug", subcategory_slug)):
            if value is not None and not isinstance(value, str):
                raise _bad(f"{prefix}{name}", "must be text")
        if subcategory_slug is not None:
            sub = self.subs.get(subcategory_slug)
            if sub is None:
                raise _bad(f"{prefix}subcategory_slug", "unknown subcategory")
            if category_slug is not None and self.category_slug_by_id.get(sub.category_id) != category_slug:
                raise _bad(f"{prefix}subcategory_slug", "does not belong to the category")
            return sub.category_id, sub.id, sub.default_flag
        cat = self.categories.get(category_slug)
        if cat is None:
            raise _bad(f"{prefix}category_slug", "unknown category")
        return cat.id, None, None


def _local_today(db, user_id: int, now: datetime) -> date:
    tz_name = db.execute(select(User.timezone).where(User.id == user_id)).scalar()
    if tz_name is None:
        raise NotFound("user not found")
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("unknown time zone on a user row; using %s", DEFAULT_TIMEZONE)
        tz = ZoneInfo(DEFAULT_TIMEZONE)
    return now.astimezone(tz).date()


def _check_not_future(value: date, today: date, field: str):
    if value > today + timedelta(days=MAX_FUTURE_DAYS):
        raise _bad(field, "is in the future")


def _select_expenses():
    return (
        select(Expense, Category.slug.label("category_slug"), Subcategory.slug.label("subcategory_slug"))
        .join(Category, Category.id == Expense.category_id)
        .outerjoin(Subcategory, Subcategory.id == Expense.subcategory_id)
    )


def _to_out(row) -> ExpenseOut:
    e = row[0]
    return ExpenseOut(
        id=e.id, client_id=e.client_id, amount_paisa=int(e.amount_paisa), occurred_on=e.occurred_on,
        category_slug=row.category_slug, subcategory_slug=row.subcategory_slug, note=e.note,
        payment_method=e.payment_method, source=e.source, shariah_flag=e.shariah_flag,
        shariah_flag_dismissed=bool(e.shariah_flag_dismissed), version=e.version,
        created_at=_aware(e.created_at), updated_at=_aware(e.updated_at),
    )


def _fetch_live(db, user_id: int, expense_id: int) -> Optional[ExpenseOut]:
    row = db.execute(_select_expenses().where(
        Expense.user_id == user_id, Expense.id == expense_id, Expense.deleted_at.is_(None))).first()
    return _to_out(row) if row else None


# ── 1. add_expenses ──────────────────────────────────────────────────────────────────────────────

def add_expenses(db, user_id: int, items: Sequence[ExpenseIn | dict], *, source: Source = "manual",
                 commit: bool = True) -> list[AddResult]:
    """
    Create up to 20 expenses in one transaction. All items are validated before anything is written.
    Safe to retry: an item whose client_id was already used is not written again (created=False).
    """
    _check_user_id(user_id)
    if source not in _ACTOR_FOR_SOURCE:
        raise _bad("source", "unknown source")
    if not isinstance(items, (list, tuple)) or not items:
        raise _bad("items", "at least one item is required")
    if len(items) > MAX_ITEMS_PER_ADD:
        raise _bad("items", f"at most {MAX_ITEMS_PER_ADD} items per call")

    parsed: list[ExpenseIn] = []
    problems: list[dict] = []
    for i, item in enumerate(items):
        try:
            parsed.append(_coerce(ExpenseIn, item, f"items[{i}]"))
        except InvalidInput as exc:
            problems += [{"field": f"items[{i}].{e['field']}", "problem": e["problem"]} for e in exc.errors]
    if problems:
        raise InvalidInput(problems)

    now = _utcnow()
    today = _local_today(db, user_id, now)
    tax = _Taxonomy(db)
    rows = []
    for i, item in enumerate(parsed):
        _check_not_future(item.occurred_on, today, f"items[{i}].occurred_on")
        category_id, sub_id, flag = tax.resolve(item.category_slug, item.subcategory_slug, f"items[{i}].")
        rows.append(dict(
            user_id=user_id, client_id=item.client_id, amount_paisa=item.amount_paisa, occurred_on=item.occurred_on,
            category_id=category_id, subcategory_id=sub_id, note=item.note, payment_method=item.payment_method,
            source=source, shariah_flag=flag, created_at=now, updated_at=now,
        ))

    actor = _ACTOR_FOR_SOURCE[source]
    results: list[AddResult] = []
    with _unit(db, commit):
        for values in rows:
            new_id = db.execute(_insert_ignore(db).values(**values).returning(Expense.id)).scalar()
            if new_id is not None:
                _audit(db, user_id, actor, "expense.create", new_id, {"source": source})
                results.append(AddResult(client_id=values["client_id"], created=True, expense=_fetch_live(db, user_id, new_id)))
                continue
            # client_id already used: report the earlier row, or that it was deleted since (never resurrect it)
            earlier = db.execute(select(Expense.id, Expense.deleted_at).where(
                Expense.user_id == user_id, Expense.client_id == values["client_id"])).first()
            if earlier is not None and earlier.deleted_at is None:
                results.append(AddResult(client_id=values["client_id"], created=False, expense=_fetch_live(db, user_id, earlier.id)))
            else:
                results.append(AddResult(client_id=values["client_id"], created=False, already_deleted=True))
    return results


# ── 2. update_expense ────────────────────────────────────────────────────────────────────────────

def update_expense(db, user_id: int, expense_id: int, expected_version: int, changes: ExpenseUpdate | dict, *,
                   actor: Actor = "user", commit: bool = True) -> ExpenseOut:
    """
    Change fields of one expense. `expected_version` is the version the caller last read; if the row has
    moved on, nothing is changed and VersionConflict is raised (the caller must re-read and retry).
    """
    _check_user_id(user_id)
    if isinstance(expense_id, bool) or not isinstance(expense_id, int) or expense_id < 1:
        raise _bad("expense_id", "must be a positive integer")
    if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 1:
        raise _bad("expected_version", "must be a positive integer")
    _check_actor(actor)

    change = _coerce(ExpenseUpdate, changes)
    given = set(change.model_fields_set)
    if not given:
        raise _bad("changes", "nothing to change")
    for name in ("amount_paisa", "occurred_on", "payment_method"):
        if name in given and getattr(change, name) is None:
            raise _bad(name, "cannot be cleared")

    now = _utcnow()
    values: dict = {}
    if "amount_paisa" in given:
        values["amount_paisa"] = change.amount_paisa
    if "payment_method" in given:
        values["payment_method"] = change.payment_method
    if "note" in given:
        values["note"] = change.note
    if "occurred_on" in given:
        _check_not_future(change.occurred_on, _local_today(db, user_id, now), "occurred_on")
        values["occurred_on"] = change.occurred_on
    if given & {"category_slug", "subcategory_slug"}:
        tax = _Taxonomy(db)
        category_id, sub_id, flag = tax.resolve(change.category_slug, change.subcategory_slug, "")
        # a new category/subcategory starts with that subcategory's default flag and an un-dismissed state
        values.update(category_id=category_id, subcategory_id=sub_id, shariah_flag=flag, shariah_flag_dismissed=False)

    values.update(updated_at=now, version=Expense.version + 1)
    with _unit(db, commit):
        stmt = (
            update(Expense)
            .where(Expense.id == expense_id, Expense.user_id == user_id,
                   Expense.deleted_at.is_(None), Expense.version == expected_version)
            .values(**values).returning(Expense.version)
            .execution_options(synchronize_session=False)
        )
        new_version = db.execute(stmt).scalar()
        if new_version is None:
            current = db.execute(select(Expense.version).where(
                Expense.id == expense_id, Expense.user_id == user_id, Expense.deleted_at.is_(None))).scalar()
            if current is None:
                raise NotFound("expense not found")
            raise VersionConflict(current)
        fields = sorted(given)
        _audit(db, user_id, actor, "expense.update", expense_id, {"fields": fields, "version": new_version})
        result = _fetch_live(db, user_id, expense_id)
    return result


# ── 3 and 4. delete_expenses / restore_expenses ──────────────────────────────────────────────────

def _flip_deleted(db, user_id: int, expense_ids, *, deleting: bool, actor: str, commit: bool) -> DeleteResult:
    _check_user_id(user_id)
    _check_actor(actor)
    ids = _check_ids(expense_ids)
    now = _utcnow()
    state = Expense.deleted_at.is_(None) if deleting else Expense.deleted_at.is_not(None)
    with _unit(db, commit):
        stmt = (
            update(Expense)
            .where(Expense.user_id == user_id, Expense.id.in_(ids), state)
            .values(deleted_at=now if deleting else None, updated_at=now, version=Expense.version + 1)
            .returning(Expense.id)
            .execution_options(synchronize_session=False)
        )
        changed = sorted(r[0] for r in db.execute(stmt))
        for expense_id in changed:
            _audit(db, user_id, actor, "expense.delete" if deleting else "expense.undo", expense_id)
    return DeleteResult(changed=changed, not_found=[i for i in ids if i not in changed])


def delete_expenses(db, user_id: int, expense_ids: Sequence[int], *, actor: Actor = "user",
                    commit: bool = True) -> DeleteResult:
    """Soft delete (undo with restore_expenses). Ids that are not this user's, or already deleted, are reported."""
    return _flip_deleted(db, user_id, expense_ids, deleting=True, actor=actor, commit=commit)


def restore_expenses(db, user_id: int, expense_ids: Sequence[int], *, actor: Actor = "user",
                     commit: bool = True) -> DeleteResult:
    """Undo a soft delete. Rows already purged (after 30 days) no longer exist and are reported as not found."""
    return _flip_deleted(db, user_id, expense_ids, deleting=False, actor=actor, commit=commit)


# ── 5. get_expenses ──────────────────────────────────────────────────────────────────────────────

def get_expenses(db, user_id: int, *, date_from: Optional[date] = None, date_to: Optional[date] = None,
                 category_slug: Optional[str] = None, subcategory_slug: Optional[str] = None,
                 expense_ids: Optional[Sequence[int]] = None, limit: int = DEFAULT_PAGE_SIZE,
                 before: Optional[tuple[date, int]] = None) -> ExpensePage:
    """
    Newest first (occurred_on, then id). Window is [date_from, date_to). For the next page pass the
    previous page's `next_before` as `before`.
    """
    _check_user_id(user_id)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE_SIZE:
        raise _bad("limit", f"must be between 1 and {MAX_PAGE_SIZE}")
    conditions = [Expense.user_id == user_id, Expense.deleted_at.is_(None)]
    if date_from is not None:
        conditions.append(Expense.occurred_on >= _check_date("date_from", date_from))
    if date_to is not None:
        conditions.append(Expense.occurred_on < _check_date("date_to", date_to))
    if date_from is not None and date_to is not None and date_from > date_to:
        raise _bad("date_from", "must not be after date_to")
    if category_slug is not None or subcategory_slug is not None:
        tax = _Taxonomy(db)
        category_id, sub_id, _flag = tax.resolve(category_slug, subcategory_slug, "")
        conditions.append(Expense.category_id == category_id)
        if sub_id is not None:
            conditions.append(Expense.subcategory_id == sub_id)
    if expense_ids is not None:
        conditions.append(Expense.id.in_(_check_ids(expense_ids)))
    if before is not None:
        try:
            b_date, b_id = before
            _check_date("before", b_date)
            if isinstance(b_id, bool) or not isinstance(b_id, int):
                raise ValueError
        except (TypeError, ValueError, InvalidInput):
            raise _bad("before", "must be the next_before value of the previous page") from None
        conditions.append(or_(Expense.occurred_on < b_date, and_(Expense.occurred_on == b_date, Expense.id < b_id)))

    rows = db.execute(
        _select_expenses().where(*conditions)
        .order_by(Expense.occurred_on.desc(), Expense.id.desc()).limit(limit + 1)
    ).all()
    items = [_to_out(r) for r in rows[:limit]]
    more = len(rows) > limit
    return ExpensePage(items=items, next_before=(items[-1].occurred_on, items[-1].id) if more else None)


# ── 6. summarize ─────────────────────────────────────────────────────────────────────────────────

def summarize(db, user_id: int, *, date_from: date, date_to: date, group_by: GroupBy = "none",
              category_slug: Optional[str] = None) -> Summary:
    """Totals in integer paisa for [date_from, date_to), computed by SQL, grouped as asked."""
    _check_user_id(user_id)
    _check_date("date_from", date_from)
    _check_date("date_to", date_to)
    if date_from > date_to:
        raise _bad("date_from", "must not be after date_to")
    if group_by not in ("none", "category", "subcategory", "day", "month"):
        raise _bad("group_by", "unknown grouping")

    conditions = [Expense.user_id == user_id, Expense.deleted_at.is_(None),
                  Expense.occurred_on >= date_from, Expense.occurred_on < date_to]
    if category_slug is not None:
        category_id, _sub, _flag = _Taxonomy(db).resolve(category_slug, None, "")
        conditions.append(Expense.category_id == category_id)

    total = cast(func.coalesce(func.sum(Expense.amount_paisa), 0), BigInteger)
    count = func.count(Expense.id)
    overall = db.execute(select(total, count).where(*conditions)).one()

    groups: list[SummaryGroup] = []
    if group_by == "category":
        q = (select(Category.slug, Category.name_en, Category.name_ur, total, count)
             .select_from(Expense)
             .join(Category, Category.id == Expense.category_id).where(*conditions)
             .group_by(Category.id, Category.slug, Category.name_en, Category.name_ur)
             .order_by(total.desc(), Category.slug))
        groups = [SummaryGroup(key=r[0], label_en=r[1], label_ur=r[2], total_paisa=int(r[3]), count=r[4])
                  for r in db.execute(q)]
    elif group_by == "subcategory":
        q = (select(Category.slug, Category.name_en, Category.name_ur,
                    Subcategory.slug, Subcategory.name_en, Subcategory.name_ur, total, count)
             .select_from(Expense)
             .join(Category, Category.id == Expense.category_id)
             .outerjoin(Subcategory, Subcategory.id == Expense.subcategory_id).where(*conditions)
             .group_by(Category.id, Category.slug, Category.name_en, Category.name_ur,
                       Subcategory.id, Subcategory.slug, Subcategory.name_en, Subcategory.name_ur)
             .order_by(total.desc(), Category.slug, Subcategory.slug))
        for r in db.execute(q):
            if r[3] is None:    # no subcategory chosen: report it under its category
                groups.append(SummaryGroup(key=f"{r[0]}.unspecified", label_en=r[1], label_ur=r[2],
                                           total_paisa=int(r[6]), count=r[7]))
            else:
                groups.append(SummaryGroup(key=r[3], label_en=r[4], label_ur=r[5], total_paisa=int(r[6]), count=r[7]))
    elif group_by == "day":
        q = (select(Expense.occurred_on, total, count).where(*conditions)
             .group_by(Expense.occurred_on).order_by(Expense.occurred_on))
        groups = [SummaryGroup(key=r[0].isoformat(), total_paisa=int(r[1]), count=r[2]) for r in db.execute(q)]
    elif group_by == "month":
        year = cast(func.extract("year", Expense.occurred_on), Integer)
        month = cast(func.extract("month", Expense.occurred_on), Integer)
        q = select(year, month, total, count).where(*conditions).group_by(year, month).order_by(year, month)
        groups = [SummaryGroup(key=f"{r[0]:04d}-{r[1]:02d}", total_paisa=int(r[2]), count=r[3]) for r in db.execute(q)]

    return Summary(date_from=date_from, date_to=date_to, total_paisa=int(overall[0]), count=overall[1],
                   group_by=group_by, groups=groups)


# ── 7. list_categories ───────────────────────────────────────────────────────────────────────────

def list_categories(db) -> list[CategoryOut]:
    """The fixed category list with its subcategories. Not user data, so it takes no user_id."""
    subs: dict[int, list[SubcategoryOut]] = {}
    for s in db.execute(select(Subcategory).order_by(Subcategory.sort_order, Subcategory.id)).scalars():
        subs.setdefault(s.category_id, []).append(SubcategoryOut(
            slug=s.slug, name_en=s.name_en, name_ur=s.name_ur, charity_type=s.charity_type, default_flag=s.default_flag))
    return [
        CategoryOut(slug=c.slug, name_en=c.name_en, name_ur=c.name_ur, is_charity=bool(c.is_charity),
                    subcategories=subs.get(c.id, []))
        for c in db.execute(select(Category).order_by(Category.sort_order, Category.id)).scalars()
    ]


# ── export (data rights) ─────────────────────────────────────────────────────────────────────────

def export_expenses(db, user_id: int, *, batch_size: int = EXPORT_BATCH):
    """
    Yield EVERY expense row this user still has in the database, oldest first: live rows and soft-deleted rows
    that are not purged yet (those carry `deleted_at`). Notes are included. Rows of other users are never read:
    the query filters on user_id. Read in keyset batches so a big history does not need one huge query.
    """
    _check_user_id(user_id)
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
        raise _bad("batch_size", "must be a positive integer")
    last = None
    while True:
        conditions = [Expense.user_id == user_id]
        if last is not None:
            conditions.append(or_(Expense.occurred_on > last[0],
                                  and_(Expense.occurred_on == last[0], Expense.id > last[1])))
        rows = db.execute(
            select(Expense, Category.slug, Category.name_en, Subcategory.slug, Subcategory.name_en)
            .join(Category, Category.id == Expense.category_id)
            .outerjoin(Subcategory, Subcategory.id == Expense.subcategory_id)
            .where(*conditions)
            .order_by(Expense.occurred_on, Expense.id).limit(batch_size)
        ).all()
        for e, cat_slug, cat_name, sub_slug, sub_name in rows:
            yield ExportRow(
                id=e.id, client_id=e.client_id, occurred_on=e.occurred_on, amount_paisa=int(e.amount_paisa),
                currency=e.currency, category_slug=cat_slug, category_name_en=cat_name,
                subcategory_slug=sub_slug, subcategory_name_en=sub_name, note=e.note,
                payment_method=e.payment_method, source=e.source, shariah_flag=e.shariah_flag,
                shariah_flag_dismissed=bool(e.shariah_flag_dismissed), version=e.version,
                created_at=_aware(e.created_at), updated_at=_aware(e.updated_at),
                deleted_at=None if e.deleted_at is None else _aware(e.deleted_at),
            )
        if len(rows) < batch_size:
            return
        last = (rows[-1][0].occurred_on, rows[-1][0].id)


# ── purge and maintenance (system functions: they act on all users, so they take no user_id) ──────

def purge_deleted_expenses(db, *, retention_days: int = PURGE_AFTER_DAYS, commit: bool = True) -> int:
    """
    Really delete expenses that were soft-deleted MORE than `retention_days` days ago (strictly older: a row deleted
    exactly 30 days ago can still be undone). Live rows are never touched. Returns how many rows were removed and,
    if any, writes one audit row (actor system, a count only).
    """
    if isinstance(retention_days, bool) or not isinstance(retention_days, int) or retention_days < 1:
        raise _bad("retention_days", "must be a whole number of days, at least 1")
    cutoff = _utcnow() - timedelta(days=retention_days)
    with _unit(db, commit):
        result = db.execute(
            delete(Expense).where(Expense.deleted_at.is_not(None), Expense.deleted_at < cutoff)
            .execution_options(synchronize_session=False)
        )
        removed = result.rowcount or 0
        if removed:
            _audit(db, None, "system", "expense.purge", None, {"count": removed})
    return removed


def _insert_run_ignore(db):
    name = db.get_bind().dialect.name
    if name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise RuntimeError(f"unsupported database: {name}")
    return insert(MaintenanceRun).on_conflict_do_nothing(index_elements=["name"])


def run_maintenance(db) -> dict:
    """
    Run the purge at most once per MAINTENANCE_INTERVAL. There is no scheduler on a free host, so normal traffic
    starts it. The "claim" is one atomic statement, so two workers or two requests at once purge only once. It never
    raises: a failure is logged (no data in the message) and the next try is after the interval.
    Returns {"ran": bool, "purged": int}.
    """
    try:
        now = _utcnow()
        last = db.execute(select(MaintenanceRun.last_run_at).where(MaintenanceRun.name == MAINTENANCE_PURGE)).scalar()
        if last is not None and _aware(last) > now - MAINTENANCE_INTERVAL:
            return {"ran": False, "purged": 0}
        # The claim is decided by RETURNING (a row comes back only for the one statement that really changed or created
        # the row); rowcount is not reliable for INSERT ... ON CONFLICT DO NOTHING.
        claimed = db.execute(
            update(MaintenanceRun)
            .where(MaintenanceRun.name == MAINTENANCE_PURGE, MaintenanceRun.last_run_at <= now - MAINTENANCE_INTERVAL)
            .values(last_run_at=now).returning(MaintenanceRun.name).execution_options(synchronize_session=False)
        ).first() is not None
        if not claimed:
            claimed = db.execute(
                _insert_run_ignore(db).values(name=MAINTENANCE_PURGE, last_run_at=now).returning(MaintenanceRun.name)
            ).first() is not None
        db.commit()
        if not claimed:
            return {"ran": False, "purged": 0}
        return {"ran": True, "purged": purge_deleted_expenses(db)}
    except Exception:                                    # housekeeping must never break a request
        db.rollback()
        log.warning("expense maintenance failed; it will be tried again after the interval", exc_info=False)
        return {"ran": False, "purged": 0}
