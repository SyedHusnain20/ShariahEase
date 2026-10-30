"""Phase 0: user columns, cascade deletes, refresh_tokens, webhook_events, maintenance_runs.

On PostgreSQL the three legacy foreign keys are replaced by ON DELETE CASCADE ones.
On SQLite (local dev and offline tests only) that step is skipped: SQLite cannot alter
a constraint without rebuilding the table, and the ORM relationships already cascade.

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_LEGACY = (
    ("zakat_records", "zakat_records_user_id_fkey"),
    ("screener_history", "screener_history_user_id_fkey"),
    ("chat_messages", "chat_messages_user_id_fkey"),
)


def _is_pg() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    # Plain ADD COLUMN works on both PostgreSQL and SQLite (no table rebuild, so data and FKs are safe).
    op.add_column("users", sa.Column("timezone", sa.String(), nullable=False, server_default="Asia/Karachi"))
    op.add_column("users", sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("users", sa.Column("email_verified_at", sa.DateTime(timezone=True)))
    op.add_column("users", sa.Column("privacy_accepted_at", sa.DateTime(timezone=True)))

    if _is_pg():
        for table, fk in _LEGACY:
            op.drop_constraint(fk, table, type_="foreignkey")
            op.create_foreign_key(fk, table, "users", ["user_id"], ["id"], ondelete="CASCADE")

    big = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
    op.create_table(
        "refresh_tokens",
        sa.Column("id", big, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("family_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("replaced_by", sa.BigInteger(), sa.ForeignKey("refresh_tokens.id", ondelete="SET NULL")),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_family_id", "refresh_tokens", ["family_id"])

    op.create_table(
        "webhook_events",
        sa.Column("id", big, primary_key=True, autoincrement=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("event_type", sa.String()),
        sa.Column("signature_valid", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="received"),
        sa.Column("payload", sa.JSON().with_variant(postgresql.JSONB(), "postgresql")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text()),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("provider", "event_id", name="uq_webhook_provider_event"),
        sa.CheckConstraint("status IN ('received','processed','failed','ignored')", name="ck_webhook_status"),
    )

    op.create_table(
        "maintenance_runs",
        sa.Column("name", sa.String(), primary_key=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("maintenance_runs")
    op.drop_table("webhook_events")
    op.drop_index("ix_refresh_tokens_family_id", "refresh_tokens")
    op.drop_index("ix_refresh_tokens_user_id", "refresh_tokens")
    op.drop_table("refresh_tokens")

    if _is_pg():
        for table, fk in _LEGACY:
            op.drop_constraint(fk, table, type_="foreignkey")
            op.create_foreign_key(fk, table, "users", ["user_id"], ["id"])

    for col in ("privacy_accepted_at", "email_verified_at", "is_admin", "token_version", "timezone"):
        if _is_pg():
            op.drop_column("users", col)
        else:
            op.execute(f"ALTER TABLE users DROP COLUMN {col}")   # SQLite 3.35+
