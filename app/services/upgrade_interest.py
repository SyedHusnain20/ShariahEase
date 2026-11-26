"""
The "Notify me about Pro" button (Path step 5b).

One tap records ONE audit_log row per person: action "upgrade_interest", actor "user", the person's id, nothing else
(no entity, no detail, no email, no payment data). The decision gate counts distinct non-admin users with such a row
(scripts/gate_metrics.sql), so a second tap must not add a second row: the answer is the same "thanks" either way.

The lock on the user's own row makes two taps that arrive at the same moment (two tabs, a double tap) end up with one
row on PostgreSQL (SQLite has one writer at a time anyway). No migration is needed: audit_log.action is a free string.
"""
from sqlalchemy import null, select

from app.database.models import AuditLog, User

ACTION = "upgrade_interest"


def has_recorded(db, user_id: int) -> bool:
    """Did this person already tap the button?"""
    row = db.execute(select(AuditLog.id).where(AuditLog.user_id == user_id, AuditLog.action == ACTION).limit(1)).first()
    return row is not None


def record_upgrade_interest(db, user_id: int) -> bool:
    """Writes the row once per person. Returns True when this call wrote it, False when it was already there."""
    db.execute(select(User.id).where(User.id == user_id).with_for_update()).first()      # serialise taps of the same person
    if has_recorded(db, user_id):
        db.rollback()                                                                   # release the lock; nothing changed
        return False
    db.add(AuditLog(user_id=user_id, actor="user", action=ACTION, entity_type=None, entity_id=None, detail=null()))     # a real SQL NULL, not a JSON null
    db.commit()
    return True
