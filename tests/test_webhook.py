"""WhatsApp webhook: signature, verify token, replay protection, log privacy."""
import hashlib
import hmac
import json

import pytest

from app.database import models
from app.routers import webhook as webhook_router
from app.services import webhook_service as ws

SECRET = "app-secret-for-tests"


def sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def payload(wamid="wamid.AAA", sender="923001234567", text="hello"):
    return {"entry": [{"changes": [{"value": {"messages": [
        {"from": sender, "id": wamid, "type": "text", "text": {"body": text}}]}}]}]}


@pytest.fixture
def handled(monkeypatch):
    calls = []

    async def fake_handle(sender, message_type, msg):
        calls.append((sender, message_type))

    monkeypatch.setattr("app.services.whatsapp_service.handle_message", fake_handle)
    return calls


def post(client, data, secret=SECRET, header=None):
    body = json.dumps(data).encode()
    headers = {"Content-Type": "application/json"}
    if header is not False:
        headers["X-Hub-Signature-256"] = header if header is not None else sign(body, secret)
    return client.post("/webhook", content=body, headers=headers)


# ── pure helpers ───────────────────────────────────────────────────────────────

def test_verify_signature_unit():
    body = b'{"a":1}'
    assert ws.verify_signature(SECRET, body, sign(body))
    assert not ws.verify_signature(SECRET, body, sign(body, "other"))
    assert not ws.verify_signature(SECRET, body + b" ", sign(body))
    assert not ws.verify_signature(SECRET, body, "")
    assert not ws.verify_signature(SECRET, body, sign(body).replace("sha256=", ""))
    assert not ws.verify_signature("", body, sign(body, ""))


@pytest.mark.parametrize("supplied,configured,prod,ok", [
    ("tok", "tok", True, True),
    ("tok", "other", True, False),
    (ws.DEFAULT_VERIFY_TOKEN, ws.DEFAULT_VERIFY_TOKEN, True, False),      # placeholder never accepted in production
    (ws.DEFAULT_VERIFY_TOKEN, ws.DEFAULT_VERIFY_TOKEN, False, True),      # fine on a dev machine
    ("x", "", True, False),
])
def test_verify_token_rules(supplied, configured, prod, ok):
    assert ws.verify_token_ok(supplied, configured, prod) is ok


def test_mask_phone():
    assert webhook_router.mask_phone("923001234567") == "***4567"
    assert "92300" not in webhook_router.mask_phone("+92 300 1234567")
    assert webhook_router.mask_phone("") == "***"


# ── signature enforcement ──────────────────────────────────────────────────────

def test_valid_signature_is_processed(client, handled, monkeypatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SECRET)
    assert post(client, payload()).status_code == 200
    assert handled == [("923001234567", "text")]


@pytest.mark.parametrize("env", ["production", "development"])
def test_wrong_or_missing_signature_is_403_when_a_secret_is_set(client, handled, monkeypatch, env):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SECRET)
    monkeypatch.setenv("ENV", env)
    assert post(client, payload(), header=sign(b"x", "wrong")).status_code == 403
    assert post(client, payload(), header=False).status_code == 403          # no header at all
    assert post(client, payload(), header="sha256=").status_code == 403
    assert handled == []


def test_production_without_a_secret_refuses_everything(client, handled, monkeypatch):
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    monkeypatch.setenv("ENV", "production")
    assert post(client, payload(), header=False).status_code == 403
    assert post(client, payload(), header=sign(b"x", "anything")).status_code == 403
    assert handled == []


def test_development_without_a_secret_accepts_unsigned(client, handled, monkeypatch):
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    monkeypatch.setenv("ENV", "development")
    assert post(client, payload(), header=False).status_code == 200
    assert len(handled) == 1


def test_invalid_json_is_400_after_a_valid_signature(client, monkeypatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SECRET)
    body = b"not json"
    r = client.post("/webhook", content=body, headers={"X-Hub-Signature-256": sign(body)})
    assert r.status_code == 400


def test_status_updates_are_acknowledged_and_ignored(client, handled, monkeypatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SECRET)
    status_only = {"entry": [{"changes": [{"value": {"statuses": [{"id": "x", "status": "delivered"}]}}]}]}
    assert post(client, status_only).status_code == 200
    assert handled == []


# ── replay protection ──────────────────────────────────────────────────────────

def test_replayed_event_is_processed_once(client, handled, monkeypatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SECRET)
    for _ in range(3):
        assert post(client, payload("wamid.ONCE")).status_code == 200
    assert len(handled) == 1
    with client.session_factory() as db:
        row = db.query(models.WebhookEvent).one()
        assert (row.provider, row.event_id, row.status, row.signature_valid) == ("whatsapp", "wamid.ONCE", "processed", True)
        assert row.payload is None                                           # message text is never stored


def test_different_events_are_each_processed(client, handled, monkeypatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SECRET)
    post(client, payload("wamid.1")); post(client, payload("wamid.2"))
    assert len(handled) == 2


def test_failed_event_can_be_retried_up_to_the_limit(client, monkeypatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SECRET)
    attempts = []

    async def flaky(sender, message_type, msg):
        attempts.append(1)
        raise RuntimeError("secret user text: my salary is 1 lakh")

    monkeypatch.setattr("app.services.whatsapp_service.handle_message", flaky)
    codes = [post(client, payload("wamid.FAIL")).status_code for _ in range(5)]
    assert codes == [500, 500, 500, 200, 200]                                # 3 attempts, then treated as duplicate
    assert len(attempts) == ws.MAX_ATTEMPTS
    with client.session_factory() as db:
        row = db.query(models.WebhookEvent).one()
        assert row.status == "failed" and row.error == "RuntimeError"        # class name only, never the message


def test_recovery_after_a_failure_marks_processed(client, monkeypatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SECRET)
    state = {"fail": True}

    async def handler(sender, message_type, msg):
        if state["fail"]:
            raise RuntimeError("boom")

    monkeypatch.setattr("app.services.whatsapp_service.handle_message", handler)
    assert post(client, payload("wamid.R")).status_code == 500
    state["fail"] = False
    assert post(client, payload("wamid.R")).status_code == 200
    with client.session_factory() as db:
        assert db.query(models.WebhookEvent).one().status == "processed"


# ── GET handshake and log privacy ───────────────────────────────────────────────

def test_verify_handshake(client, monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("VERIFY_TOKEN", "real-token")
    ok = client.get("/webhook", params={"hub.mode": "subscribe", "hub.challenge": "42", "hub.verify_token": "real-token"})
    assert (ok.status_code, ok.text) == (200, "42")
    bad = client.get("/webhook", params={"hub.mode": "subscribe", "hub.challenge": "42", "hub.verify_token": "nope"})
    assert bad.status_code == 403


def test_logs_never_contain_the_phone_number_or_message_text(client, handled, monkeypatch, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SECRET)
    post(client, payload("wamid.LOG", sender="923001234567", text="my salary is 123456"))
    assert "923001234567" not in caplog.text and "123456" not in caplog.text
    assert "***4567" in caplog.text
