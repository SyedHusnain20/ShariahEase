from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


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

    cash: float = Field(default=0.0, ge=0)
    gold_value: float = Field(default=0.0, ge=0)
    silver_value: float = Field(default=0.0, ge=0)
    business_inventory: float = Field(default=0.0, ge=0)
    receivables: float = Field(default=0.0, ge=0)

    debts: float = Field(default=0.0, ge=0)
    immediate_expenses: float = Field(default=0.0, ge=0)

    nisab_rate_used: str = Field(default="gold")


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

class ScreenerResponse(BaseModel):
    query: str
    verdict: str
    explanation: str
    confidence: str

    class Config:
        from_attributes = True


# ── CHATBOT SCHEMAS ───────────────────────────────────────

class ChatRequest(BaseModel):
    session_id: str
    message: str = Field(..., min_length=1, max_length=1000)
    language: str = "en"

class ChatResponse(BaseModel):
    session_id: str
    answer: str
    language: str
    sources: list = []

    class Config:
        from_attributes = True


# ── MUDARABAH SCHEMAS ─────────────────────────────────────

class MudarabahRequest(BaseModel):
    principal: float = Field(..., gt=0)
    profit_rate: float = Field(..., gt=0, le=100)
    investor_share: float = Field(..., gt=0, lt=100)
    period_months: int = Field(..., gt=0, le=360)

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
