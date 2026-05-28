import re
from pydantic import BaseModel, Field, field_validator, EmailStr
from typing import Optional
from datetime import datetime


# ── SANITIZATION HELPER ───────────────────────────────────
def strip_html(value: str) -> str:
    """Remove HTML/script tags and control characters from string inputs."""
    # Strip HTML tags
    value = re.sub(r"<[^>]+>", "", value)
    # Remove null bytes and other control chars (keep newlines for chat)
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)
    return value.strip()


# ── USER SCHEMAS ──────────────────────────────────────────

class UserCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    email: Optional[str] = None
    language: str = "en"

class UserResponse(BaseModel):
    id: int
    name: str
    email: Optional[str]
    language: str
    created_at: datetime

    class Config:
        from_attributes = True


# ── ZAKAT SCHEMAS ─────────────────────────────────────────

class ZakatRequest(BaseModel):
    user_name: str = Field(..., min_length=2, max_length=100)

    # All monetary values: non-negative, max 10 billion PKR (sanity cap)
    cash:               float = Field(default=0.0, ge=0, le=10_000_000_000)
    gold_value:         float = Field(default=0.0, ge=0, le=10_000_000_000)
    silver_value:       float = Field(default=0.0, ge=0, le=10_000_000_000)
    business_inventory: float = Field(default=0.0, ge=0, le=10_000_000_000)
    receivables:        float = Field(default=0.0, ge=0, le=10_000_000_000)
    debts:              float = Field(default=0.0, ge=0, le=10_000_000_000)
    immediate_expenses: float = Field(default=0.0, ge=0, le=10_000_000_000)

    nisab_rate_used: str = Field(default="gold")

    @field_validator("user_name")
    @classmethod
    def clean_name(cls, v):
        return strip_html(v)

    @field_validator("nisab_rate_used")
    @classmethod
    def validate_nisab_rate(cls, v):
        if v not in ("gold", "silver"):
            return "gold"
        return v


class ZakatResult(BaseModel):
    user_name: str
    total_assets: float
    total_deductions: float
    zakatable_wealth: float
    nisab_threshold: float
    zakat_due: float
    is_zakat_due: bool
    breakdown: dict

    class Config:
        from_attributes = True


class ZakatRecordResponse(BaseModel):
    id: int
    user_name: str
    zakatable_wealth: float
    zakat_due: float
    is_zakat_due: bool
    calculated_at: datetime

    class Config:
        from_attributes = True


# ── SCREENER SCHEMAS ──────────────────────────────────────

class ScreenerRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=300)

    @field_validator("query")
    @classmethod
    def clean_query(cls, v):
        return strip_html(v)

class ScreenerResponse(BaseModel):
    query: str
    verdict: str
    explanation: str
    confidence: str

    class Config:
        from_attributes = True


# ── CHATBOT SCHEMAS ───────────────────────────────────────

class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=100)
    message:    str = Field(..., min_length=1, max_length=2000)
    language:   str = Field(default="en", max_length=10)

    @field_validator("message")
    @classmethod
    def clean_message(cls, v):
        return strip_html(v)

    @field_validator("session_id")
    @classmethod
    def clean_session_id(cls, v):
        # Session IDs must be alphanumeric + hyphens/underscores only
        if not re.match(r"^[a-zA-Z0-9_\-]{1,100}$", v):
            raise ValueError("Invalid session_id format")
        return v

class ChatResponse(BaseModel):
    session_id: str
    answer: str
    language: str
    sources: list = []
    audio_b64: str = ""

    class Config:
        from_attributes = True


# ── MUDARABAH SCHEMAS ─────────────────────────────────────

class MudarabahRequest(BaseModel):
    principal:      float = Field(..., gt=0,    le=10_000_000_000)
    profit_rate:    float = Field(..., gt=0,    le=100)
    investor_share: float = Field(..., gt=0,    lt=100)
    period_months:  int   = Field(..., gt=0,    le=360)

class ScenarioResult(BaseModel):
    label: str
    gross_profit: float
    investor_profit: float
    bank_profit: float
    total_return: float
    monthly_return: float

class MudarabahResponse(BaseModel):
    principal: float
    investor_share: float
    bank_share: float
    period_months: int
    scenarios: list[ScenarioResult]
