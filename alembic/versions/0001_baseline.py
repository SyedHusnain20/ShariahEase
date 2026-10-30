"""Baseline: the four tables ShariahEase had before migrations existed.

Databases that already have these tables (everything created by the old
Base.metadata.create_all) are *stamped* with this revision instead of running it;
see app/database/migrate.py.

Revision ID: 0001
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("email", sa.String(150), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("language", sa.String(10)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_id", "users", ["id"])

    op.create_table(
        "zakat_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", name="zakat_records_user_id_fkey")),
        sa.Column("user_name", sa.String(100), nullable=False),
        sa.Column("cash", sa.Float()),
        sa.Column("gold_value", sa.Float()),
        sa.Column("silver_value", sa.Float()),
        sa.Column("business_inventory", sa.Float()),
        sa.Column("receivables", sa.Float()),
        sa.Column("debts", sa.Float()),
        sa.Column("immediate_expenses", sa.Float()),
        sa.Column("total_assets", sa.Float(), nullable=False),
        sa.Column("total_deductions", sa.Float(), nullable=False),
        sa.Column("zakatable_wealth", sa.Float(), nullable=False),
        sa.Column("nisab_threshold", sa.Float(), nullable=False),
        sa.Column("zakat_due", sa.Float(), nullable=False),
        sa.Column("is_zakat_due", sa.Boolean()),
        sa.Column("nisab_rate_used", sa.String(20)),
        sa.Column("calculated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_zakat_records_user_id", "zakat_records", ["user_id"])
    op.create_index("ix_zakat_records_id", "zakat_records", ["id"])

    op.create_table(
        "screener_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", name="screener_history_user_id_fkey")),
        sa.Column("query", sa.String(300), nullable=False),
        sa.Column("verdict", sa.String(20), nullable=False),
        sa.Column("explanation", sa.Text()),
        sa.Column("screened_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_screener_history_id", "screener_history", ["id"])
    op.create_index("ix_screener_history_user_id", "screener_history", ["user_id"])

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", name="chat_messages_user_id_fkey")),
        sa.Column("session_id", sa.String(100), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("language", sa.String(10)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_chat_messages_user_id", "chat_messages", ["user_id"])
    op.create_index("ix_chat_messages_session_id", "chat_messages", ["session_id"])
    op.create_index("ix_chat_messages_id", "chat_messages", ["id"])


def downgrade() -> None:
    for t in ("chat_messages", "screener_history", "zakat_records", "users"):
        op.drop_table(t)
