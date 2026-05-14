from fastapi import APIRouter, Depends, HTTPException, status, Response, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr, Field

from app.database.db import get_db
from app.database import models
from app.services.auth_service import hash_password, verify_password, create_access_token
from app.dependencies import get_current_user

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

class TokenResponse(BaseModel):
    access_token: str
    token_type:   str = "bearer"
    user_id:      int
    name:         str
    email:        str


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

    token = create_access_token({"sub": str(user.id)})

    # Also set cookie so Jinja2 page loads work
    response.set_cookie("access_token", token, httponly=True, samesite="lax", max_age=60*60*24*7)

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

    token = create_access_token({"sub": str(user.id)})
    response.set_cookie("access_token", token, httponly=True, samesite="lax", max_age=60*60*24*7)

    return TokenResponse(
        access_token = token,
        user_id      = user.id,
        name         = user.name,
        email        = user.email,
    )


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie("access_token")
    return {"detail": "Logged out"}


@router.get("/me")
def me(current_user: models.User = Depends(get_current_user)):
    return {
        "id":         current_user.id,
        "name":       current_user.name,
        "email":      current_user.email,
        "language":   current_user.language,
        "created_at": current_user.created_at,
    }
