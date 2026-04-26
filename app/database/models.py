from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Boolean
from sqlalchemy.sql import func
from app.database.db import Base


class User(Base):
    """
    Optional user tracking — stores name for PDF certificate.
    No login system needed for a competition project.
    """
    __tablename__ = "users"

    id         = Column(Integer, primary_key=True, index=True)
    name       = Column(String(100), nullable=False)
    email      = Column(String(150), nullable=True)
    language   = Column(String(10), default="en")       # 'en' or 'ur'
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ZakatRecord(Base):
    """
    Saves every Zakat calculation — shown in history on the calculator page.
    """
    __tablename__ = "zakat_records"

    id              = Column(Integer, primary_key=True, index=True)
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


class ScreenerHistory(Base):
    """
    Saves every halal screening query and its verdict.
    """
    __tablename__ = "screener_history"

    id           = Column(Integer, primary_key=True, index=True)
    query        = Column(String(300), nullable=False)   # company/fund name entered
    verdict      = Column(String(20), nullable=False)    # 'halal', 'haram', 'doubtful'
    explanation  = Column(Text, nullable=True)           # Groq's full explanation
    screened_at  = Column(DateTime(timezone=True), server_default=func.now())


class ChatMessage(Base):
    """
    Stores chatbot conversation history per session.
    """
    __tablename__ = "chat_messages"

    id         = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(100), nullable=False, index=True)
    role       = Column(String(20), nullable=False)   # 'user' or 'assistant'
    content    = Column(Text, nullable=False)
    language   = Column(String(10), default="en")
    created_at = Column(DateTime(timezone=True), server_default=func.now())