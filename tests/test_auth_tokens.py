"""Secret handling and access tokens (PyJWT)."""
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.services import auth_service as auth
from app.services.auth_service import (
    create_access_token, decode_token, resolve_secret_key, _DEV_KEY,
)

GOOD = "k" * 40


# ── fail closed in production ──────────────────────────────────────────────────

@pytest.mark.parametrize("env", [
    {"ENV": "production"},
    {"ENV": "production", "SECRET_KEY": ""},
    {"ENV": "production", "SECRET_KEY": "   "},
    {"ENV": "production", "SECRET_KEY": _DEV_KEY},
    {"ENV": "production", "SECRET_KEY": "short-key"},
    {"ENV": "production", "SECRET_KEY": "k" * 31},
])
def test_production_refuses_missing_or_weak_secret(env):
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        resolve_secret_key(env)


def test_production_accepts_a_real_secret():
    assert resolve_secret_key({"ENV": "production", "SECRET_KEY": GOOD}) == GOOD
    assert resolve_secret_key({"ENV": "production", "SECRET_KEY": "k" * 32}) == "k" * 32


def test_development_falls_back_with_a_warning(caplog):
    assert resolve_secret_key({}) == _DEV_KEY
    assert resolve_secret_key({"ENV": "development", "SECRET_KEY": "abc"}) == "abc"
    assert "SECRET_KEY not set" in caplog.text


# ── access tokens ──────────────────────────────────────────────────────────────

def test_round_trip_and_claims():
    token = create_access_token({"sub": "7", "tv": 3})
    p = decode_token(token)
    assert p["sub"] == "7" and p["tv"] == 3 and p["typ"] == "access"
    assert 0 < (datetime.fromtimestamp(p["exp"], timezone.utc) - datetime.now(timezone.utc)).total_seconds() <= 15 * 60


def test_expired_token_is_rejected():
    assert decode_token(create_access_token({"sub": "1", "tv": 0}, timedelta(seconds=-5))) is None


def test_token_signed_with_another_key_is_rejected():
    forged = jwt.encode({"sub": "1", "tv": 0, "typ": "access",
                         "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}, "other-key-" * 5, algorithm="HS256")
    assert decode_token(forged) is None


def test_alg_none_is_rejected():
    forged = jwt.encode({"sub": "1", "tv": 0, "typ": "access",
                         "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}, None, algorithm="none")
    assert decode_token(forged) is None


def test_token_without_expiry_is_rejected():
    assert decode_token(jwt.encode({"sub": "1", "tv": 0, "typ": "access"}, auth.SECRET_KEY, algorithm="HS256")) is None


def test_token_that_is_not_an_access_token_is_rejected():
    soon = datetime.now(timezone.utc) + timedelta(minutes=5)
    assert decode_token(jwt.encode({"sub": "1", "tv": 0, "exp": soon}, auth.SECRET_KEY, algorithm="HS256")) is None
    assert decode_token(jwt.encode({"sub": "1", "tv": 0, "typ": "refresh", "exp": soon}, auth.SECRET_KEY, algorithm="HS256")) is None


def test_garbage_is_rejected():
    assert decode_token("not.a.token") is None
    assert decode_token("") is None


def test_only_hs256_is_accepted_even_with_the_right_key():
    soon = datetime.now(timezone.utc) + timedelta(minutes=5)
    other_alg = jwt.encode({"sub": "1", "tv": 0, "typ": "access", "exp": soon}, auth.SECRET_KEY, algorithm="HS384")
    assert decode_token(other_alg) is None
    assert decode_token(jwt.encode({"sub": "1", "tv": 0, "typ": "access", "exp": soon}, auth.SECRET_KEY, algorithm="HS256")) is not None
