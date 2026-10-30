"""Webhook helpers: Meta signature check and replay protection (webhook_events table)."""
import hashlib
import hmac
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.models import WebhookEvent

MAX_ATTEMPTS = 3
DEFAULT_VERIFY_TOKEN = "shariahease-CHANGE-ME"


def verify_signature(secret: str, body: bytes, header: str) -> bool:
    """Meta signs the raw body: X-Hub-Signature-256 = 'sha256=' + HMAC-SHA256(app secret, body)."""
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def verify_token_ok(supplied: str, configured: str, production: bool) -> bool:
    """GET /webhook handshake. In production the default placeholder token is never accepted."""
    if production and (not configured or configured == DEFAULT_VERIFY_TOKEN):
        return False
    return hmac.compare_digest(supplied.encode(), configured.encode())


def claim_event(db: Session, provider: str, event_id: str, event_type: str | None,
                signature_valid: bool) -> str:
    """
    Record an incoming event. Returns:
      "new"        first time we see it: process it
      "retry"      we saw it before and processing failed (and attempts remain): process again
      "duplicate"  already handled or in progress: do nothing
    """
    try:
        db.add(WebhookEvent(provider=provider, event_id=event_id, event_type=event_type,
                            signature_valid=signature_valid, attempts=1))
        db.commit()
        return "new"
    except IntegrityError:
        db.rollback()

    claimed = db.query(WebhookEvent).filter(
        WebhookEvent.provider == provider, WebhookEvent.event_id == event_id,
        WebhookEvent.status == "failed", WebhookEvent.attempts < MAX_ATTEMPTS,
    ).update({WebhookEvent.status: "received", WebhookEvent.attempts: WebhookEvent.attempts + 1},
             synchronize_session=False)
    db.commit()
    return "retry" if claimed == 1 else "duplicate"


def finish_event(db: Session, provider: str, event_id: str, ok: bool, error: str | None = None) -> None:
    db.query(WebhookEvent).filter(
        WebhookEvent.provider == provider, WebhookEvent.event_id == event_id
    ).update({
        WebhookEvent.status: "processed" if ok else "failed",
        WebhookEvent.processed_at: datetime.now(timezone.utc) if ok else None,
        WebhookEvent.error: error,
    }, synchronize_session=False)
    db.commit()
