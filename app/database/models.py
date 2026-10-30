from sqlalchemy import (
    Column, Integer, BigInteger, String, Float, DateTime, Text, Boolean, ForeignKey,
    JSON, Uuid, UniqueConstraint, CheckConstraint, text,
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
