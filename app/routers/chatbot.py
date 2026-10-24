from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
import base64

from app.database.db import get_db
from app.database.crud import save_message, get_chat_history
from app.database import models
from app.models.schemas import ChatRequest, ChatResponse
from app.services.chat_graph import run_chat
from app.services.llm_client import detect_language
from app.services.tts_service import text_to_speech_bytes
from app.dependencies import get_optional_user

router = APIRouter(prefix="/chat", tags=["Chatbot"])

@router.post("/message", response_model=ChatResponse)
async def chat_message(
    request: ChatRequest,
    db: Session = Depends(get_db),
    # Optional auth: logged-in users get saved history, guests can still chat.
    current_user: models.User | None = Depends(get_optional_user),
):
    # None for guests, user id for logged-in users
    uid = current_user.id if current_user else None

    save_message(db, request.session_id, "user", request.message, user_id=uid)

    # Fetch history after saving, exclude the just-saved user message
    all_records     = get_chat_history(db, request.session_id, limit=13, user_id=uid)
    history_records = all_records[:-1]
    chat_history    = [
        {"role": r.role, "content": r.content}
        for r in history_records
    ]

    # Language guard, retrieval (RAG + web + live Nisab), generation and the
    # answer checks all live in app/services/chat_graph.py.
    result       = await run_chat(request.message, chat_history)
    answer       = result["answer"]
    source_names = result["sources"]

    save_message(db, request.session_id, "assistant", answer, user_id=uid)

    # ── TTS — generate audio for every assistant response ─────────────────
    # Detect language from the answer (more reliable than request language)
    # so Urdu answers get the Urdu voice and English answers get English voice.
    audio_b64 = ""
    if not answer.startswith("⚠️"):   # skip TTS for error messages
        try:
            lang        = detect_language(answer)
            audio_bytes = await text_to_speech_bytes(answer, lang)
            if audio_bytes:
                audio_b64 = base64.b64encode(audio_bytes).decode()
        except Exception:
            pass   # TTS failure is non-fatal — text response still returned

    return ChatResponse(
        session_id = request.session_id,
        answer     = answer,
        language   = "auto",
        sources    = source_names,
        audio_b64  = audio_b64,
    )


@router.get("/history/{session_id}")
async def get_history(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: models.User | None = Depends(get_optional_user),
):
    # Guests have no saved history
    if not current_user:
        return []

    # Only return messages belonging to this user's session
    records = get_chat_history(db, session_id, limit=50, user_id=current_user.id)
    return [
        {"role": r.role, "content": r.content,
         "created_at": r.created_at.strftime("%I:%M %p")}
        for r in records
    ]


@router.delete("/history/{session_id}")
async def clear_history(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: models.User | None = Depends(get_optional_user),
):
    from app.database.models import ChatMessage

    q = db.query(ChatMessage).filter(ChatMessage.session_id == session_id)
    if current_user:
        # Only delete messages owned by this user
        q = q.filter(ChatMessage.user_id == current_user.id)
    else:
        # Guests can only clear guest (user_id NULL) messages
        q = q.filter(ChatMessage.user_id.is_(None))
    q.delete(synchronize_session=False)
    db.commit()
    return {"cleared": True}
