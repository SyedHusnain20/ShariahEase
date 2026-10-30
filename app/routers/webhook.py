"""WhatsApp webhook (moved out of main.py so it can be tested; behaviour is hardened, see README_PHASE0)."""
import hashlib
import json
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.services import webhook_service

logger = logging.getLogger(__name__)
router = APIRouter()

PROVIDER = "whatsapp"


def _is_production() -> bool:
    return os.getenv("ENV", "development") == "production"


def mask_phone(number: str) -> str:
    """Keep only the last 4 digits in logs: phone numbers are personal data."""
    digits = "".join(ch for ch in str(number) if ch.isdigit())
    return "***" + digits[-4:] if digits else "***"


@router.get("/webhook")
async def verify(
    hub_mode: str          = Query(alias="hub.mode"),
    hub_challenge: str     = Query(alias="hub.challenge"),
    hub_verify_token: str  = Query(alias="hub.verify_token"),
):
    configured = os.getenv("VERIFY_TOKEN", webhook_service.DEFAULT_VERIFY_TOKEN)
    if webhook_service.verify_token_ok(hub_verify_token, configured, _is_production()):
        return PlainTextResponse(hub_challenge)
    logger.warning("Invalid webhook verify token received")
    return PlainTextResponse("Forbidden", status_code=403)


@router.post("/webhook")
async def receive(request: Request, db: Session = Depends(get_db)):
    body   = await request.body()
    secret = os.getenv("WHATSAPP_APP_SECRET", "")

    if secret:
        if not webhook_service.verify_signature(secret, body, request.headers.get("X-Hub-Signature-256", "")):
            logger.warning("Invalid or missing WhatsApp webhook signature")
            raise HTTPException(status_code=403, detail="Invalid signature")
        signature_valid = True
    elif _is_production():
        # Unsigned requests can be forged by anyone, so production refuses them.
        logger.error("WHATSAPP_APP_SECRET is not set: refusing webhook requests in production")
        raise HTTPException(status_code=403, detail="Webhook not configured")
    else:
        logger.warning("WHATSAPP_APP_SECRET not set: accepting UNSIGNED webhook (development only)")
        signature_valid = False

    try:
        data = json.loads(body)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    logger.info("WhatsApp webhook received")
    try:
        msg          = data["entry"][0]["changes"][0]["value"]["messages"][0]
        sender       = msg["from"]
        message_type = msg.get("type", "text")
    except (KeyError, IndexError, TypeError):
        return JSONResponse({"status": "ok"})   # not a message event (delivery receipt, status update)

    event_id = str(msg.get("id") or hashlib.sha256(body).hexdigest())
    state = webhook_service.claim_event(db, PROVIDER, event_id, message_type, signature_valid)
    if state == "duplicate":
        logger.info("Duplicate WhatsApp event ignored")
        return JSONResponse({"status": "ok"})

    logger.info("Message from %s type=%s", mask_phone(sender), message_type)
    try:
        from app.services.whatsapp_service import handle_message
        await handle_message(sender, message_type, msg)
    except Exception as exc:
        # Log the error class only: the message text of an exception can contain user content.
        webhook_service.finish_event(db, PROVIDER, event_id, ok=False, error=type(exc).__name__)
        logger.error("WhatsApp handler failed: %s", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Processing failed")   # Meta will retry
    webhook_service.finish_event(db, PROVIDER, event_id, ok=True)
    return JSONResponse({"status": "ok"})
