from sqlalchemy import (
    Column, Integer, SmallInteger, BigInteger, String, Float, DateTime, Date, Text, Boolean, ForeignKey,
    ForeignKeyConstraint, Index, JSON, Uuid, UniqueConstraint, CheckConstraint, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database.db import Base


class User(Base):
    __tablename__ = "users"

    id               = Column(Integer, primary_key=True, index=True)
    name             = Column(String(100), nullable=False)
    email            = Column(String(150), unique=True, nullable=False, index=True)
    hashed_password  = Column(String(255), nullable=False)
    language         = Column(String(10), default="en")
    created_at       = Column(DateTime(timezone=True), server_default=func.now())

    # Added in Phase 0 (see alembic/versions/0002_phase0_platform.py)
    timezone            = Column(String, nullable=False, server_default="Asia/Karachi")
    token_version       = Column(Integer, nullable=False, server_default="0")   # bump = "log out everywhere"
    is_admin            = Column(Boolean, nullable=False, server_default=text("false"))
    email_verified_at   = Column(DateTime(timezone=True), nullable=True)
    privacy_accepted_at = Column(DateTime(timezone=True), nullable=True)

    zakat_records    = relationship("ZakatRecord",     back_populates="user", cascade="all, delete-orphan")
    screener_history = relationship("ScreenerHistory", back_populates="user", cascade="all, delete-orphan")
    chat_messages    = relationship("ChatMessage",     back_populates="user", cascade="all, delete-orphan")


class ZakatRecord(Base):
    """
    Saves every Zakat calculation — shown in history on the calculator page.
    """
    __tablename__ = "zakat_records"

    id              = Column(Integer, primary_key=True, index=True)
    user_id         = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    user_name       = Column(String(100), nullable=False)

    # Assets
    cash            = Column(Float, default=0.0)
    gold_value      = Column(Float, default=0.0)
    silver_value    = Column(Float, default=0.0)
    business_inventory = Column(Float, default=0.0)
    receivables     = Column(Float, default=0.0)

    # Deductions
    debts           = Column(Float, default=0.0)
    immediate_expenses = Column(Float, default=0.0)

    # Calculated results
    total_assets    = Column(Float, nullable=False)
    total_deductions = Column(Float, nullable=False)
    zakatable_wealth = Column(Float, nullable=False)
    nisab_threshold = Column(Float, nullable=False)
    zakat_due       = Column(Float, nullable=False)
    is_zakat_due    = Column(Boolean, default=True)

    # Metadata
    nisab_rate_used = Column(String(20), default="gold")  # 'gold' or 'silver'
    calculated_at   = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="zakat_records")


class ScreenerHistory(Base):
    """
    Saves every halal screening query and its verdict.
    """
    __tablename__ = "screener_history"

    id          = Column(Integer, primary_key=True, index=True)
    user_id     = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    query       = Column(String(300), nullable=False)
    verdict     = Column(String(20), nullable=False)
    explanation = Column(Text, nullable=True)
    screened_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="screener_history")


class ChatMessage(Base):
    """
    Stores chatbot conversation history per session.
    """
    __tablename__ = "chat_messages"

    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    session_id = Column(String(100), nullable=False, index=True)
    role       = Column(String(20), nullable=False)
    content    = Column(Text, nullable=False)
    language   = Column(String(10), default="en")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="chat_messages")


# Primary key type that is BIGINT on Postgres but still autoincrements on SQLite
# (a plain BigInteger primary key does not autoincrement on SQLite).
BigPK = BigInteger().with_variant(Integer, "sqlite")


class RefreshToken(Base):
    """
    One row per issued refresh token. Only the SHA-256 of the token is stored.
    One login = one family. Presenting an already-rotated token revokes the family.
    """
    __tablename__ = "refresh_tokens"

    id          = Column(BigPK, primary_key=True, autoincrement=True)
    user_id     = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    family_id   = Column(Uuid, nullable=False, index=True)
    token_hash  = Column(String(64), nullable=False, unique=True)
    issued_at   = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at  = Column(DateTime(timezone=True), nullable=False)
    revoked_at  = Column(DateTime(timezone=True), nullable=True)
    replaced_by = Column(BigInteger, ForeignKey("refresh_tokens.id", ondelete="SET NULL"), nullable=True)


class WebhookEvent(Base):
    """Webhook intake and replay protection (WhatsApp now, billing later)."""
    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint("provider", "event_id", name="uq_webhook_provider_event"),
        CheckConstraint("status IN ('received','processed','failed','ignored')", name="ck_webhook_status"),
    )

    id              = Column(BigPK, primary_key=True, autoincrement=True)
    provider        = Column(String, nullable=False)
    event_id        = Column(String, nullable=False)
    event_type      = Column(String, nullable=True)
    signature_valid = Column(Boolean, nullable=False)
    status          = Column(String, nullable=False, server_default="received")
    payload         = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)      # stays NULL for WhatsApp: message text is personal data
    attempts        = Column(Integer, nullable=False, server_default="0")
    error           = Column(Text, nullable=True)
    received_at     = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    processed_at    = Column(DateTime(timezone=True), nullable=True)


class MaintenanceRun(Base):
    """Throttle for opportunistic cleanup (there is no scheduler on a free host)."""
    __tablename__ = "maintenance_runs"

    name        = Column(String, primary_key=True)
    last_run_at = Column(DateTime(timezone=True), nullable=False)


# ── Phase 1: expense tracker (see alembic/versions/0004_phase1_expenses.py) ───────────────────────

SmallPK = SmallInteger().with_variant(Integer, "sqlite")

MAX_AMOUNT_PAISA = 10 ** 12          # PKR 10 billion, same sanity cap as the Zakat calculator
MAX_NOTE_CHARS = 500


class Category(Base):
    """Fixed list shipped with the app (seeded by migration 0004). Slugs are stable keys."""
    __tablename__ = "categories"

    id         = Column(SmallPK, primary_key=True, autoincrement=True)
    slug       = Column(String, nullable=False, unique=True)
    name_en    = Column(String, nullable=False)
    name_ur    = Column(String, nullable=False)
    is_charity = Column(Boolean, nullable=False, server_default=text("false"))
    sort_order = Column(SmallInteger, nullable=False, server_default="0")


class Subcategory(Base):
    __tablename__ = "subcategories"
    __table_args__ = (
        UniqueConstraint("id", "category_id", name="uq_subcategories_id_category"),   # target of the composite FK on expenses
        CheckConstraint("charity_type IN ('zakat','sadaqah','fitrana','other')", name="ck_subcategories_charity_type"),
        CheckConstraint("default_flag IN ('riba','doubtful')", name="ck_subcategories_default_flag"),
    )

    id           = Column(SmallPK, primary_key=True, autoincrement=True)
    category_id  = Column(SmallInteger, ForeignKey("categories.id"), nullable=False)
    slug         = Column(String, nullable=False, unique=True)          # e.g. 'charity.zakat'
    name_en      = Column(String, nullable=False)
    name_ur      = Column(String, nullable=False)
    charity_type = Column(String, nullable=True)                        # drives the yearly Zakat/Sadaqah statement
    default_flag = Column(String, nullable=True)                        # rule-based and conservative, never a fatwa
    sort_order   = Column(SmallInteger, nullable=False, server_default="0")


class Expense(Base):
    """
    One expense. Money is integer paisa (no floats). occurred_on is the user's LOCAL date.
    Every query on this table must filter on user_id (the service layer does) and on deleted_at.
    """
    __tablename__ = "expenses"
    __table_args__ = (
        ForeignKeyConstraint(["subcategory_id", "category_id"], ["subcategories.id", "subcategories.category_id"],
                             name="fk_expenses_subcategory_category"),   # a subcategory must belong to the category
        UniqueConstraint("user_id", "client_id", name="uq_expenses_user_client"),
        CheckConstraint(f"amount_paisa BETWEEN 1 AND {MAX_AMOUNT_PAISA}", name="ck_expenses_amount"),
        CheckConstraint("currency = 'PKR'", name="ck_expenses_currency"),
        CheckConstraint("occurred_on >= '2000-01-01'", name="ck_expenses_date_floor"),
        CheckConstraint(f"length(note) <= {MAX_NOTE_CHARS}", name="ck_expenses_note_len"),
        CheckConstraint("payment_method IN ('cash','bank_transfer','card','wallet','other')", name="ck_expenses_payment_method"),
        CheckConstraint("source IN ('manual','ai_chat','whatsapp','pwa_sync')", name="ck_expenses_source"),
        CheckConstraint("shariah_flag IN ('riba','doubtful')", name="ck_expenses_shariah_flag"),
        Index("ix_exp_user_date", "user_id", text("occurred_on DESC"), text("id DESC"),
              postgresql_where=text("deleted_at IS NULL"), sqlite_where=text("deleted_at IS NULL")),
        Index("ix_exp_user_cat_date", "user_id", "category_id", text("occurred_on DESC"),
              postgresql_where=text("deleted_at IS NULL"), sqlite_where=text("deleted_at IS NULL")),
        Index("ix_exp_user_sub_date", "user_id", "subcategory_id", "occurred_on",
              postgresql_where=text("deleted_at IS NULL AND subcategory_id IS NOT NULL"),
              sqlite_where=text("deleted_at IS NULL AND subcategory_id IS NOT NULL")),
        Index("ix_exp_purge", "deleted_at",
              postgresql_where=text("deleted_at IS NOT NULL"), sqlite_where=text("deleted_at IS NOT NULL")),
    )

    id             = Column(BigPK, primary_key=True, autoincrement=True)
    user_id        = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    client_id      = Column(Uuid, nullable=False)                       # idempotency key (retries, double taps, WhatsApp redelivery)
    amount_paisa   = Column(BigInteger, nullable=False)
    currency       = Column(String(3), nullable=False, server_default="PKR")
    occurred_on    = Column(Date, nullable=False)
    category_id    = Column(SmallInteger, ForeignKey("categories.id"), nullable=False)
    subcategory_id = Column(SmallInteger, nullable=True)
    note           = Column(Text, nullable=True)                        # untrusted text; never logged, never sent to the model
    payment_method = Column(String, nullable=False, server_default="cash")
    source         = Column(String, nullable=False, server_default="manual")
    shariah_flag   = Column(String, nullable=True)
    shariah_flag_dismissed = Column(Boolean, nullable=False, server_default=text("false"))
    version        = Column(Integer, nullable=False, server_default="1")   # optimistic concurrency
    created_at     = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at     = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    deleted_at     = Column(DateTime(timezone=True), nullable=True)     # soft delete; purged after 30 days (step 11)


class AuditLog(Base):
    """Who did what. `detail` holds ids, field NAMES and counts only: never note text or amounts."""
    __tablename__ = "audit_log"
    __table_args__ = (
        CheckConstraint("actor IN ('user','ai','whatsapp','admin','system')", name="ck_audit_actor"),
        Index("ix_audit_user_time", "user_id", text("created_at DESC")),
    )

    id          = Column(BigPK, primary_key=True, autoincrement=True)
    user_id     = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    actor       = Column(String, nullable=False)
    action      = Column(String, nullable=False)                        # 'expense.create', 'expense.update', ...
    entity_type = Column(String, nullable=True)
    entity_id   = Column(String, nullable=True)
    detail      = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    created_at  = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
