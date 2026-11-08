"""
Account deletion.

Deleting the user row is the whole job; everything that belongs to the user goes with it:

  * zakat_records, screener_history, chat_messages: ON DELETE CASCADE on PostgreSQL (migration 0002), and the ORM
    relationships on User (cascade="all, delete-orphan") on SQLite, where 0002 cannot alter the constraint. That is why the
    row is deleted through the ORM below and not with a bare `DELETE FROM users`, which fails on SQLite for these three.
  * refresh_tokens, expenses, audit_log: ON DELETE CASCADE in the database.

tests/test_account_deletion.py checks every table that points at users.id and that none has a row left after a deletion,
so a future table that forgets its cascade fails the tests (and tests/test_account_deletion_pg.py checks the strict
database-level cascade on a real PostgreSQL).

Things that are not in our database and therefore not deleted here (the privacy page says so): text already sent to
Groq or a web search to answer a chat question, opt-in LangSmith traces (off unless the operator enables them), and
Neon's own short-lived history of the database.
"""
from app.database.models import User


def delete_account(db, user_id: int) -> bool:
    """Delete the user and, through the cascades, all of their data. True if a user was deleted."""
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 1:
        raise TypeError("user_id must come from the authenticated session as a positive int")
    try:
        user = db.get(User, user_id)
        if user is None:
            return False
        db.delete(user)                       # ORM cascades for the three legacy tables, database cascades for the rest
        db.commit()
    except Exception:
        db.rollback()
        raise
    return True
