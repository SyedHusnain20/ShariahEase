"""
Rotating refresh tokens.

* The raw token is random and shown to the browser once (in an httpOnly cookie).
  Only its SHA-256 is stored.
* One login = one family. Each use of a refresh token revokes it and issues the
  next one in the same family.
* Presenting a token that was already rotated is treated as theft: the whole
  family is revoked. Exception: a second request within REUSE_GRACE_SECONDS
  (two browser tabs refreshing at once) is rejected without revoking anything.
"""
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.database.models import RefreshToken
from app.services.auth_service import REFRESH_TOKEN_EXPIRE_DAYS

REUSE_GRACE_SECONDS = 10


class InvalidRefreshToken(Exception):
    """The token is unknown, expired, revoked or reused."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    # SQLite hands back naive datetimes; everything we store is UTC.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def issue_refresh_token(db: Session, user_id: int, family_id: uuid.UUID | None = None,
                        now: datetime | None = None) -> str:
    """Create and store a new refresh token; returns the raw value (never stored)."""
    now = now or _now()
    raw = secrets.token_urlsafe(48)
    db.add(RefreshToken(
        user_id=user_id,
        family_id=family_id or uuid.uuid4(),
        token_hash=hash_token(raw),
        issued_at=now,
        expires_at=now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
    ))
    db.commit()
    return raw


def revoke_family(db: Session, family_id, now: datetime | None = None) -> None:
    now = now or _now()
    db.query(RefreshToken).filter(
        RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None)
    ).update({RefreshToken.revoked_at: now}, synchronize_session=False)
    db.commit()


def revoke_all_for_user(db: Session, user_id: int, now: datetime | None = None) -> None:
    now = now or _now()
    db.query(RefreshToken).filter(
        RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
    ).update({RefreshToken.revoked_at: now}, synchronize_session=False)
    db.commit()


def revoke_presented_token(db: Session, raw: str | None) -> None:
    """Logout: revoke the whole family of the presented token (no error if unknown)."""
    if not raw:
        return
    row = db.query(RefreshToken).filter(RefreshToken.token_hash == hash_token(raw)).first()
    if row:
        revoke_family(db, row.family_id)


def rotate_refresh_token(db: Session, raw: str | None, now: datetime | None = None) -> tuple[int, str]:
    """Validate and rotate. Returns (user_id, new_raw_token) or raises InvalidRefreshToken."""
    now = now or _now()
    if not raw:
        raise InvalidRefreshToken("missing")

    row = db.query(RefreshToken).filter(RefreshToken.token_hash == hash_token(raw)).first()
    if row is None:
        raise InvalidRefreshToken("unknown")

    if row.revoked_at is not None:
        recently = (now - _aware(row.revoked_at)).total_seconds() <= REUSE_GRACE_SECONDS
        if recently and row.replaced_by is not None:
            raise InvalidRefreshToken("concurrent refresh")      # two tabs at once: no punishment
        revoke_family(db, row.family_id, now)                     # reuse of an old token: assume theft
        raise InvalidRefreshToken("reuse detected")

    if _aware(row.expires_at) <= now:
        raise InvalidRefreshToken("expired")

    # Claim the token atomically: only one concurrent request can flip revoked_at from NULL.
    claimed = db.query(RefreshToken).filter(
        RefreshToken.id == row.id, RefreshToken.revoked_at.is_(None)
    ).update({RefreshToken.revoked_at: now}, synchronize_session=False)
    if claimed != 1:
        db.rollback()
        raise InvalidRefreshToken("concurrent refresh")

    new_raw = secrets.token_urlsafe(48)
    new_row = RefreshToken(
        user_id=row.user_id, family_id=row.family_id, token_hash=hash_token(new_raw),
        issued_at=now, expires_at=now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
    )
    db.add(new_row)
    db.flush()
    db.query(RefreshToken).filter(RefreshToken.id == row.id).update(
        {RefreshToken.replaced_by: new_row.id}, synchronize_session=False)
    db.commit()
    return row.user_id, new_raw
