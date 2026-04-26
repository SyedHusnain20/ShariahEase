from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv
import os

load_dotenv()

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

from app.routers import pages, zakat, mudarabah, chatbot, screener, certificate, charities, voice
app.include_router(pages.router)
app.include_router(zakat.router)
app.include_router(mudarabah.router)
app.include_router(chatbot.router)
app.include_router(screener.router)
app.include_router(certificate.router)
app.include_router(charities.router)
app.include_router(voice.router)

@app.get("/health")
def health():
    return {"status": "ok", "rag_ready": rag_service.is_ready}
