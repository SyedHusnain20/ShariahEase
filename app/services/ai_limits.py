"""
Limits for the AI expense assistant (Path step 4b): the kill switch, the monthly free allowance, the daily token breaker.

Settings (environment, read on every call so a change on the Space needs no code change):
  EXPENSE_AI_ENABLED    "false" switches the whole assistant off (default on)
  AI_FREE_LIMIT         AI messages per person per month (default 30)
  LLM_DAILY_TOKEN_CAP   tokens the assistant may spend per UTC day, everybody together (default 120000).
                        Keep it BELOW the provider's daily cap: on the free Groq plan that is 200,000 tokens a day,
                        shared with the website chatbot.

The monthly allowance is ONE atomic statement (INSERT ... ON CONFLICT DO UPDATE ... WHERE used < limit ... RETURNING).
Reading the number and then writing it back would let 60 parallel messages all pass a limit of 30; this cannot.
A message that was not served (the provider failed, or it turned out not to be an expense) is given back with release().
"""
import os
from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select, update

from app.database.models import LlmUsageDaily, UsageCounter, User

DEFAULT_FREE_LIMIT = 30
DEFAULT_DAILY_TOKEN_CAP = 120_000
DEFAULT_TIMEZONE = "Asia/Karachi"
_FALSE = {"0", "false", "no", "off"}


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        return max(0, int(raw)) if raw else default
    except ValueError:
        return default


def ai_enabled() -> bool:
    return os.getenv("EXPENSE_AI_ENABLED", "true").strip().lower() not in _FALSE


def free_limit() -> int:
    return _int_env("AI_FREE_LIMIT", DEFAULT_FREE_LIMIT)


def daily_token_cap() -> int:
    return _int_env("LLM_DAILY_TOKEN_CAP", DEFAULT_DAILY_TOKEN_CAP)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _insert(db):
    name = db.get_bind().dialect.name
    if name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise RuntimeError(f"unsupported database: {name}")
    return insert


# ── monthly allowance ─────────────────────────────────────────────────────────────────────────────

def period_for(db, user_id: int, now: Optional[datetime] = None) -> str:
    """'YYYY-MM' in the person's own time zone: their month is the month they see on their phone."""
    now = now or _utcnow()
    tz_name = db.execute(select(User.timezone).where(User.id == user_id)).scalar() or DEFAULT_TIMEZONE
    try:
        zone = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo(DEFAULT_TIMEZONE)
    return now.astimezone(zone).strftime("%Y-%m")


def try_consume(db, user_id: int, period: str, limit: Optional[int] = None) -> Optional[int]:
    """Use one AI message. Returns how many are used now, or None when the allowance is already spent. Commits."""
    limit = free_limit() if limit is None else limit
    if limit <= 0:
        return None
    table = UsageCounter.__table__
    statement = (
        _insert(db)(table).values(user_id=user_id, period=period, used=1)
        .on_conflict_do_update(index_elements=["user_id", "period"], set_={"used": table.c.used + 1}, where=table.c.used < limit)
        .returning(table.c.used)
    )
    try:
        used = db.execute(statement).scalar()
        db.commit()
    except Exception:
        db.rollback()
        raise
    return used


def release(db, user_id: int, period: str) -> None:
    """Give one message back (it was not served). Never goes below zero. Commits."""
    try:
        db.execute(update(UsageCounter).where(UsageCounter.user_id == user_id, UsageCounter.period == period,
                                              UsageCounter.used > 0).values(used=UsageCounter.used - 1)
                   .execution_options(synchronize_session=False))
        db.commit()
    except Exception:
        db.rollback()
        raise


def used_this_period(db, user_id: int, period: str) -> int:
    return db.execute(select(UsageCounter.used).where(UsageCounter.user_id == user_id, UsageCounter.period == period)).scalar() or 0


# ── daily breaker ─────────────────────────────────────────────────────────────────────────────────

def _day(now: Optional[datetime]) -> date:
    return (now or _utcnow()).astimezone(timezone.utc).date()


def tokens_today(db, now: Optional[datetime] = None) -> int:
    return db.execute(select(LlmUsageDaily.tokens).where(LlmUsageDaily.day == _day(now))).scalar() or 0


def breaker_open(db, now: Optional[datetime] = None) -> bool:
    """True when today's tokens have reached the cap: no new model calls until the next UTC day."""
    return tokens_today(db, now) >= daily_token_cap()


def add_tokens(db, tokens: int, now: Optional[datetime] = None) -> None:
    """Add what a model call really cost (as the provider reported it). Commits."""
    tokens = max(0, int(tokens or 0))
    table = LlmUsageDaily.__table__
    day = _day(now)
    statement = (
        _insert(db)(table).values(day=day, tokens=tokens, calls=1)
        .on_conflict_do_update(index_elements=["day"], set_={"tokens": table.c.tokens + tokens, "calls": table.c.calls + 1})
    )
    try:
        db.execute(statement)
        db.commit()
    except Exception:
        db.rollback()
        raise
