"""Step 4b: the AI expense assistant's tables: pending_actions, usage_counters, llm_usage_daily.

  pending_actions  a confirmation card: what the assistant understood, waiting for the person's "yes"
                   (15-minute life, single use, one row per chat message so a retry cannot make a second card)
  usage_counters   how many AI messages a person has used this month (one row per user per month)
  llm_usage_daily  tokens spent per UTC day across everybody: the daily breaker. No user id on purpose.

Rules every user table follows: user_id with ON DELETE CASCADE, partial index on the live rows.

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pending_actions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("client_msg_id", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False),
        sa.Column("result", sa.JSON().with_variant(postgresql.JSONB(), "postgresql")),
        sa.Column("rev", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("undone_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("user_id", "client_msg_id", name="uq_pending_user_msg"),
        sa.CheckConstraint("kind IN ('create','update','delete')", name="ck_pending_kind"),
        sa.CheckConstraint("status IN ('pending','confirmed','cancelled','expired')", name="ck_pending_status"),
    )
    op.create_index("ix_pending_user_live", "pending_actions", ["user_id", sa.text("created_at DESC")],
                    postgresql_where=sa.text("status = 'pending'"), sqlite_where=sa.text("status = 'pending'"))

    op.create_table(
        "usage_counters",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("period", sa.String(7), primary_key=True),                 # 'YYYY-MM' in the person's own time zone
        sa.Column("used", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("used >= 0", name="ck_usage_used"),
    )

    op.create_table(
        "llm_usage_daily",
        sa.Column("day", sa.Date(), primary_key=True),                       # UTC day
        sa.Column("tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("calls", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("tokens >= 0", name="ck_llm_tokens"),
    )


def downgrade() -> None:
    op.drop_table("llm_usage_daily")
    op.drop_table("usage_counters")
    op.drop_index("ix_pending_user_live", "pending_actions")
    op.drop_table("pending_actions")
