"""Delete stored guest chat messages (chat_messages.user_id IS NULL).

Guest messages are no longer stored (guests get short-lived in-memory context only), so rows written
by older versions are removed. This cannot be undone: the downgrade does nothing.
Messages of logged-in users are not touched.

Revision ID: 0003
Revises: 0002
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM chat_messages WHERE user_id IS NULL")


def downgrade() -> None:
    pass   # deleted guest messages cannot be restored
