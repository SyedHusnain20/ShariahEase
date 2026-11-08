from fastapi import APIRouter, Depends, Header, HTTPException, status, Response, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.database.db import get_db
from app.database import models
from app.services.auth_service import (
    hash_password, verify_password, create_access_token,
    ACCESS_TOKEN_EXPIRE_MINUTES, REFRESH_TOKEN_EXPIRE_DAYS,
)
from app.services import account_service, refresh_tokens
from app.services.rate_limit import SlidingWindowLimiter
from app.dependencies import get_current_user
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_IS_PRODUCTION = os.getenv("ENV", "development") == "production"

# Account deletion asks for the password again. A stolen session must not be able to guess it, so attempts are limited
# per user (5 in 15 minutes, right or wrong), and the same anti-forgery header as the expense writes is required.
DELETE_ATTEMPTS = 5
DELETE_WINDOW_SECONDS = 900
delete_limiter = SlidingWindowLimiter(DELETE_ATTEMPTS, DELETE_WINDOW_SECONDS)
REQUIRED_HEADER = ("X-Requested-With", "ShariahEase")

router    = APIRouter(prefix="/auth", tags=["Auth"])
templates = Jinja2Templates(directory="frontend/templates")


# ── Schemas (local — simple enough to keep here) ──────────────────────────────

class SignupRequest(BaseModel):
    name:     str   = Field(..., min_length=2, max_length=100)
    email:    EmailStr
    password: str   = Field(..., min_length=6, max_length=128)
    language: str   = "en"

class LoginRequest(BaseModel):
    email:    EmailStr
    password: str

class DeleteAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(..., min_length=1, max_length=128)

class TokenResponse(BaseModel):
    access_token: str
    token_type:   str = "bearer"
    user_id:      int
    name:         str
    email:        str


# ── Session helpers ───────────────────────────────────────────────────────────

def _start_session(response: Response, db: Session, user: models.User, family_id=None, refresh_raw: str | None = None) -> str:
    """Issue an access token (15 min) and a refresh token (rotating, httpOnly cookie)."""
    access = create_access_token({"sub": str(user.id), "tv": user.token_version})
    if refresh_raw is None:
        refresh_raw = refresh_tokens.issue_refresh_token(db, user.id, family_id)
    response.set_cookie(
        "access_token", access, httponly=True, samesite="lax",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60, secure=_IS_PRODUCTION,
    )
    response.set_cookie(
        "refresh_token", refresh_raw, httponly=True, samesite="lax", path="/auth",
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600, secure=_IS_PRODUCTION,
    )
    return access


def _clear_session(response: Response) -> None:
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token", path="/auth")


# ── HTML pages ────────────────────────────────────────────────────────────────

@router.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse("pages/login.html", {"request": request, "active_page": "login"})

@router.get("/signup")
async def signup_page(request: Request):
    return templates.TemplateResponse("pages/signup.html", {"request": request, "active_page": "signup"})


# ── API endpoints ─────────────────────────────────────────────────────────────

@router.post("/signup", response_model=TokenResponse, status_code=201)
def signup(body: SignupRequest, response: Response, db: Session = Depends(get_db)):
    # Duplicate email check
    existing = db.query(models.User).filter(models.User.email == body.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    user = models.User(
        name            = body.name,
        email           = body.email,
        hashed_password = hash_password(body.password),
        language        = body.language,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Also sets cookies so Jinja2 page loads work
    token = _start_session(response, db, user)

    return TokenResponse(
        access_token = token,
        user_id      = user.id,
        name         = user.name,
        email        = user.email,
    )


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == body.email).first()
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = _start_session(response, db, user)

    return TokenResponse(
        access_token = token,
        user_id      = user.id,
        name         = user.name,
        email        = user.email,
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    """Swap the refresh cookie for a new access token and a new refresh cookie (rotation)."""
    raw = request.cookies.get("refresh_token")
    try:
        user_id, new_raw = refresh_tokens.rotate_refresh_token(db, raw)
    except refresh_tokens.InvalidRefreshToken:
        _clear_session(response)
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")

    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    token = _start_session(response, db, user, refresh_raw=new_raw)
    return TokenResponse(access_token=token, user_id=user.id, name=user.name, email=user.email)


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    refresh_tokens.revoke_presented_token(db, request.cookies.get("refresh_token"))
    _clear_session(response)
    return {"detail": "Logged out"}


@router.post("/logout-all")
def logout_all(response: Response, db: Session = Depends(get_db),
               current_user: models.User = Depends(get_current_user)):
    """Log out everywhere: old access tokens stop working and every refresh token is revoked."""
    current_user.token_version += 1
    db.commit()
    refresh_tokens.revoke_all_for_user(db, current_user.id)
    _clear_session(response)
    return {"detail": "Logged out on all devices"}


@router.get("/me")
def me(current_user: models.User = Depends(get_current_user)):
    return {
        "id":         current_user.id,
        "name":       current_user.name,
        "email":      current_user.email,
        "language":   current_user.language,
        "created_at": current_user.created_at,
    }


@router.delete("/me")
def delete_me(
    body: DeleteAccountRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
    x_requested_with: Optional[str] = Header(default=None),
):
    """
    Permanently delete the logged-in user's account and ALL their data (see services/account_service.py).
    Needs the password again, the anti-forgery header, and is limited to a few attempts.
    """
    if x_requested_with != REQUIRED_HEADER[1]:
        raise HTTPException(status_code=403, detail={"code": "missing_header"})
    wait = delete_limiter.check(current_user.id)
    if wait > 0:
        raise HTTPException(status_code=429, detail={"code": "rate_limited", "retry_after": int(wait) + 1},
                            headers={"Retry-After": str(int(wait) + 1)})
    if not verify_password(body.password, current_user.hashed_password):
        raise HTTPException(status_code=403, detail={"code": "wrong_password"})
    user_id = current_user.id
    if not account_service.delete_account(db, user_id):
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    _clear_session(response)
    logger.info("account deleted (user id %s)", user_id)          # an id only: no name, email or data
    return {"detail": "Account deleted"}
