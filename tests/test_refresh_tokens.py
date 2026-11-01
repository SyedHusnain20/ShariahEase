"""Refresh-token rotation, reuse detection, logout, and 'log out everywhere'."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.database import models
from app.services import refresh_tokens as rt
from app.services.auth_service import create_access_token
from tests.conftest import signup

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def user(db_session):
    u = models.User(name="A", email="a@x.com", hashed_password="h")
    db_session.add(u)
    db_session.commit()
    return u


# ── service level (time is injected, no sleeping) ─────────────────────────────

def test_only_the_hash_is_stored(db_session, user):
    raw = rt.issue_refresh_token(db_session, user.id, now=T0)
    row = db_session.query(models.RefreshToken).one()
    assert row.token_hash == rt.hash_token(raw) and raw not in (row.token_hash, str(row.family_id))
    assert len(row.token_hash) == 64


def test_rotation_issues_a_new_token_in_the_same_family(db_session, user):
    raw = rt.issue_refresh_token(db_session, user.id, now=T0)
    uid, new_raw = rt.rotate_refresh_token(db_session, raw, now=T0 + timedelta(minutes=20))
    assert uid == user.id and new_raw != raw
    rows = db_session.query(models.RefreshToken).order_by(models.RefreshToken.id).all()
    assert len(rows) == 2 and rows[0].family_id == rows[1].family_id
    assert rows[0].revoked_at is not None and rows[0].replaced_by == rows[1].id and rows[1].revoked_at is None


def test_reusing_a_rotated_token_revokes_the_whole_family(db_session, user):
    raw = rt.issue_refresh_token(db_session, user.id, now=T0)
    _, newer = rt.rotate_refresh_token(db_session, raw, now=T0 + timedelta(minutes=20))
    with pytest.raises(rt.InvalidRefreshToken, match="reuse"):
        rt.rotate_refresh_token(db_session, raw, now=T0 + timedelta(hours=1))      # the thief replays the old one
    with pytest.raises(rt.InvalidRefreshToken):
        rt.rotate_refresh_token(db_session, newer, now=T0 + timedelta(hours=1, minutes=1))   # the legit newer one is dead too
    assert db_session.query(models.RefreshToken).filter(models.RefreshToken.revoked_at.is_(None)).count() == 0


def test_second_tab_within_the_grace_window_is_rejected_but_does_not_revoke(db_session, user):
    raw = rt.issue_refresh_token(db_session, user.id, now=T0)
    _, newer = rt.rotate_refresh_token(db_session, raw, now=T0 + timedelta(minutes=20))
    with pytest.raises(rt.InvalidRefreshToken, match="concurrent"):
        rt.rotate_refresh_token(db_session, raw, now=T0 + timedelta(minutes=20, seconds=rt.REUSE_GRACE_SECONDS))
    rt.rotate_refresh_token(db_session, newer, now=T0 + timedelta(minutes=21))      # the newer token still works


def test_grace_window_ends(db_session, user):
    raw = rt.issue_refresh_token(db_session, user.id, now=T0)
    rt.rotate_refresh_token(db_session, raw, now=T0 + timedelta(minutes=20))
    with pytest.raises(rt.InvalidRefreshToken, match="reuse"):
        rt.rotate_refresh_token(db_session, raw, now=T0 + timedelta(minutes=20, seconds=rt.REUSE_GRACE_SECONDS + 1))


def test_expired_unknown_and_missing_tokens(db_session, user):
    raw = rt.issue_refresh_token(db_session, user.id, now=T0)
    with pytest.raises(rt.InvalidRefreshToken, match="expired"):
        rt.rotate_refresh_token(db_session, raw, now=T0 + timedelta(days=rt.REFRESH_TOKEN_EXPIRE_DAYS, seconds=1))
    with pytest.raises(rt.InvalidRefreshToken, match="unknown"):
        rt.rotate_refresh_token(db_session, "nope", now=T0)
    with pytest.raises(rt.InvalidRefreshToken, match="missing"):
        rt.rotate_refresh_token(db_session, None, now=T0)


def test_token_just_before_expiry_still_works(db_session, user):
    raw = rt.issue_refresh_token(db_session, user.id, now=T0)
    uid, _ = rt.rotate_refresh_token(db_session, raw, now=T0 + timedelta(days=rt.REFRESH_TOKEN_EXPIRE_DAYS) - timedelta(seconds=1))
    assert uid == user.id


def test_losing_the_atomic_claim_race_is_refused(db_session, user, monkeypatch):
    """If another request revokes the token between our read and our UPDATE, no second token may be minted."""
    from sqlalchemy.orm import sessionmaker

    raw = rt.issue_refresh_token(db_session, user.id, now=T0)
    row_id = db_session.query(models.RefreshToken).one().id
    other = sessionmaker(bind=db_session.get_bind())()
    real_aware, fired = rt._aware, []

    def aware_then_race(value):
        # _aware runs on the expiry check: after rotate has read the row, before it claims it
        if not fired:
            fired.append(True)
            other.query(models.RefreshToken).filter(models.RefreshToken.id == row_id).update(
                {models.RefreshToken.revoked_at: T0}, synchronize_session=False)
            other.commit()
        return real_aware(value)

    monkeypatch.setattr(rt, "_aware", aware_then_race)
    with pytest.raises(rt.InvalidRefreshToken, match="concurrent"):
        rt.rotate_refresh_token(db_session, raw, now=T0 + timedelta(minutes=1))
    assert fired and db_session.query(models.RefreshToken).count() == 1


# ── HTTP level ────────────────────────────────────────────────────────────────

def test_signup_sets_both_cookies_and_a_short_lived_access_token(client):
    body = signup(client)
    assert client.cookies.get("access_token") and client.cookies.get("refresh_token")
    assert "refresh_token" not in body                                   # never in JSON (XSS)
    assert client.get("/auth/me").status_code == 200


def test_refresh_endpoint_rotates_and_the_old_cookie_stops_working(client):
    signup(client)
    old_refresh = client.cookies.get("refresh_token")
    r = client.post("/auth/refresh")
    assert r.status_code == 200 and r.json()["access_token"]
    assert client.cookies.get("refresh_token") != old_refresh
    # replaying the OLD cookie after the grace window: simulate by moving the rotation timestamps back
    with client.session_factory() as db:
        for row in db.query(models.RefreshToken).filter(models.RefreshToken.revoked_at.isnot(None)):
            row.revoked_at = row.revoked_at - timedelta(minutes=5)
        db.commit()
    client.cookies.clear()                                               # one cookie only: two with the same name are sent in an unspecified order
    client.cookies.set("refresh_token", old_refresh, path="/auth")
    assert client.post("/auth/refresh").status_code == 401
    with client.session_factory() as db:                                  # theft response: family revoked
        assert db.query(models.RefreshToken).filter(models.RefreshToken.revoked_at.is_(None)).count() == 0


def test_refresh_without_a_cookie_is_401(client):
    assert client.post("/auth/refresh").status_code == 401


def test_logout_revokes_the_refresh_token(client):
    signup(client)
    old_refresh = client.cookies.get("refresh_token")
    assert client.post("/auth/logout").status_code == 200
    client.cookies.clear()                                               # one cookie only: two with the same name are sent in an unspecified order
    client.cookies.set("refresh_token", old_refresh, path="/auth")
    assert client.post("/auth/refresh").status_code == 401


def test_logout_all_kills_old_access_tokens_and_every_refresh_token(client):
    first = signup(client)
    token = first["access_token"]
    # a second device logs in
    other = client.post("/auth/login", json={"email": "a@example.com", "password": "secret123"})
    assert other.status_code == 200
    r = client.post("/auth/logout-all", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {other.json()['access_token']}"}).status_code == 401
    with client.session_factory() as db:
        assert db.query(models.RefreshToken).filter(models.RefreshToken.revoked_at.is_(None)).count() == 0
        assert db.query(models.User).one().token_version == 1


def test_access_token_with_a_stale_token_version_is_rejected(client):
    signup(client)
    uid = client.get("/auth/me").json()["id"]
    stale = create_access_token({"sub": str(uid), "tv": 99})
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {stale}"}).status_code == 401
    missing = create_access_token({"sub": str(uid)})                      # an old-style token without tv
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {missing}"}).status_code == 401


def test_protected_route_needs_a_token(client):
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_deleting_a_user_removes_their_refresh_tokens(db_session, user):
    rt.issue_refresh_token(db_session, user.id)
    db_session.delete(user)
    db_session.commit()
    assert db_session.query(models.RefreshToken).count() == 0
