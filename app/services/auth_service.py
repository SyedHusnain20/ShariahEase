import os
import logging
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from passlib.context import CryptContext

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
_DEV_KEY = "dev-only-insecure-key-SET-SECRET_KEY-IN-ENV"
_MIN_PROD_KEY_LEN = 32


def resolve_secret_key(env: Mapping) -> str:
    """
    Fail closed: in production the app refuses to start without a real secret,
    because a known default key would let anyone forge login tokens.
    Outside production a dev default is used, with a warning.
    """
    key = (env.get("SECRET_KEY") or "").strip()
    if env.get("ENV", "development") == "production":
        if not key or key == _DEV_KEY or len(key) < _MIN_PROD_KEY_LEN:
            raise RuntimeError(
                "SECRET_KEY is missing, too short (< 32 characters) or the dev default, and ENV=production. "
                "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\" "
                "and set it as a Secret on the Hugging Face Space."
            )
        return key
    if not key:
        logger.warning(
            "SECRET_KEY not set in .env — using insecure dev default. "
            "Generate one: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
        return _DEV_KEY
    return key


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(os.getenv(name, default))
    except ValueError:
        return default
    return max(lo, min(hi, value))


SECRET_KEY = resolve_secret_key(os.environ)

ALGORITHM                    = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES  = _int_env("ACCESS_TOKEN_MINUTES", 15, 1, 24 * 60)
REFRESH_TOKEN_EXPIRE_DAYS    = _int_env("REFRESH_TOKEN_DAYS", 30, 1, 365)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ── Password helpers ──────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ── JWT helpers ───────────────────────────────────────────────────────────────

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """data must contain "sub" (user id as text) and "tv" (the user's token_version)."""
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    to_encode.update({
        "iat": now,
        "exp": now + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)),
        "typ": "access",
    })
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    """Returns payload dict or None if invalid, expired or not an access token."""
    try:
        payload = jwt.decode(
            token, SECRET_KEY, algorithms=[ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError:
        return None
    if payload.get("typ") != "access":
        return None
    return payload
