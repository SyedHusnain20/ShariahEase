from fastapi import FastAPI, Query, Request
from fastapi.responses import PlainTextResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv
import os

load_dotenv()

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "shariahease123")

app = FastAPI(
    title=os.getenv("APP_NAME", "ShariahEase"),
    description="Islamic Finance AI Assistant",
    version="1.0.0"
)

app.mount("/static", StaticFiles(directory="frontend/static"), name="static")
templates = Jinja2Templates(directory="frontend/templates")

from app.database.db import engine, Base
from app.database import models
Base.metadata.create_all(bind=engine)

from app.services.rag_service import rag_service

@app.on_event("startup")
async def startup_event():
    # Load RAG index
    rag_service.load()
    # Pre-load Whisper model so first voice request is not slow
    from app.services.whisper_service import preload_model
    preload_model()

from app.routers import pages, zakat, mudarabah, chatbot, screener, certificate, charities, voice, auth
app.include_router(auth.router)
app.include_router(pages.router)
app.include_router(zakat.router)
app.include_router(mudarabah.router)
app.include_router(chatbot.router)
app.include_router(screener.router)
app.include_router(certificate.router)
app.include_router(charities.router)
app.include_router(voice.router)

# ✅ WhatsApp Webhook Verification (Meta calls this once to verify)
@app.get("/webhook")
async def verify(
    hub_mode: str = Query(alias="hub.mode"),
    hub_challenge: str = Query(alias="hub.challenge"),
    hub_verify_token: str = Query(alias="hub.verify_token")
):
    if hub_verify_token == VERIFY_TOKEN:
        return PlainTextResponse(hub_challenge)
    return PlainTextResponse("Forbidden", status_code=403)

# ✅ WhatsApp Webhook - Receive incoming messages
@app.post("/webhook")
async def receive(request: Request):
    data = await request.json()
    print("📩 RECEIVED:", data)  # add this line
    try:
        msg = data["entry"][0]["changes"][0]["value"]["messages"][0]
        sender = msg["from"]
        text = msg["text"]["body"]
        print(f"📩 MESSAGE FROM {sender}: {text}")  # add this line
        from app.services.whatsapp_service import handle_message
        await handle_message(sender, text)
    except (KeyError, IndexError):
        pass
    return JSONResponse({"status": "ok"})

@app.get("/health")
def health():
    return {"status": "ok", "rag_ready": rag_service.is_ready}
