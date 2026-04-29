"""
ShariahEase — Voice Router
Handles the full voice pipeline:
  1. Receive audio blob from browser
  2. Transcribe with Whisper
  3. Detect language
  4. RAG retrieval
  5. Groq answer
  6. edge-tts to MP3
  7. Return JSON with text + base64 audio
"""

import base64
import logging
from fastapi import APIRouter, File, UploadFile, HTTPException, Request
from fastapi.templating import Jinja2Templates

from app.services.whisper_service import transcribe_audio
from app.services.tts_service import text_to_speech_sync
from app.services.rag_service import rag_service
from app.services.llm_client import get_chat_response, detect_language
from app.services.metal_price import get_nisab_values

router    = APIRouter(prefix="/voice", tags=["Voice"])
templates = Jinja2Templates(directory="frontend/templates")
logger    = logging.getLogger(__name__)

NISAB_KEYWORDS = {
    "نصاب", "nisab", "sona", "chandi", "سونا", "چاندی",
    "tola", "gram", "how much zakat", "zakat amount",
}


def _is_nisab_question(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in NISAB_KEYWORDS)


async def _build_nisab_context() -> str:
    try:
        data         = await get_nisab_values()
        gold_nisab   = int(data["gold_nisab_pkr"])
        silver_nisab = int(data["silver_nisab_pkr"])
        gold_gram    = int(data["gold_per_gram_pkr"])
        silver_gram  = int(data["silver_per_gram_pkr"])
        return (
            f"=== LIVE NISAB DATA ===\n"
            f"Gold per gram today: PKR {gold_gram:,}\n"
            f"Silver per gram today: PKR {silver_gram:,}\n"
            f"Gold Nisab (7.5 tola = 87.48g): PKR {gold_nisab:,}\n"
            f"Silver Nisab (52.5 tola = 612.36g): PKR {silver_nisab:,}\n"
            f"Zakat rate: 2.5%\n"
            f"=== END LIVE NISAB DATA ==="
        )
    except Exception:
        return (
            "=== NISAB DATA (APPROXIMATE) ===\n"
            "Gold Nisab (7.5 tola): approx PKR 18,00,000–20,00,000\n"
            "Silver Nisab (52.5 tola): approx PKR 1,50,000–2,00,000\n"
            "Zakat rate: 2.5%\n"
            "=== END ==="
        )


@router.get("/")
async def voice_page(request: Request):
    """Serve the voice avatar page."""
    return templates.TemplateResponse("pages/voice.html", {
        "request":     request,
        "active_page": "voice",
    })


@router.post("/ask")
async def voice_ask(audio: UploadFile = File(...)):
    """
    Full voice pipeline endpoint.
    Receives audio → transcribes → answers → speaks → returns JSON.

    Response:
    {
      "transcribed_text": "what the user said",
      "answer_text":      "bot response",
      "language":         "en" | "ur" | "roman",
      "audio_b64":        "base64-encoded mp3",
      "success":          true | false,
      "error":            null | "message"
    }
    """
    try:
        # ── 1. Read audio bytes ────────────────────────────
        audio_bytes = await audio.read()
        if not audio_bytes:
            raise HTTPException(status_code=400, detail="No audio data received")

        # Get file extension from content type or filename
        content_type = audio.content_type or ""
        if "webm" in content_type or (audio.filename and "webm" in audio.filename):
            ext = "webm"
        elif "wav" in content_type:
            ext = "wav"
        elif "mp4" in content_type or "m4a" in content_type:
            ext = "mp4"
        else:
            ext = "webm"    # browser MediaRecorder default

        logger.info(f"Received audio: {len(audio_bytes)} bytes, type={ext}")

        # ── 2. Transcribe with Whisper ─────────────────────
        transcription = transcribe_audio(audio_bytes, ext)

        if not transcription["success"] or not transcription["text"].strip():
            return {
                "transcribed_text": "",
                "answer_text":      "I could not understand the audio. Please try again.",
                "language":         "en",
                "audio_b64":        "",
                "success":          False,
                "error":            transcription.get("error", "Transcription failed"),
            }

        user_text    = transcription["text"].strip()
        whisper_lang = transcription["language"]

        logger.info(f"Transcribed: '{user_text}' (lang={whisper_lang})")

        # ── 3. Detect language for response ───────────────
        # Our own detector is more reliable for Urdu/Roman/English classification
        lang = detect_language(user_text)

        # ── 4. Build context: live Nisab + RAG ────────────
        nisab_context = await _build_nisab_context()
        rag_context   = rag_service.build_context(user_text, top_k=5)
        full_context  = f"{nisab_context}\n\n{rag_context}"

        # ── 5. Generate answer via Groq ────────────────────
        try:
            answer_text = ask_groq(
                user_message     = user_text,
                context          = full_context,
                chat_history     = [],
                is_nisab_related = _is_nisab_question(user_text),
            )
        except Exception as e:
            logger.error(f"Groq error: {e}")
            answer_text = "I am having trouble connecting to the AI service. Please check your internet connection."

        logger.info(f"Answer: {answer_text[:100]}")

        # ── 6. Convert answer to speech ────────────────────
        audio_bytes_out = text_to_speech_sync(answer_text, lang)
        audio_b64       = base64.b64encode(audio_bytes_out).decode() if audio_bytes_out else ""

        return {
            "transcribed_text": user_text,
            "answer_text":      answer_text,
            "language":         lang,
            "audio_b64":        audio_b64,
            "success":          True,
            "error":            None,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Voice pipeline error: {e}", exc_info=True)
        return {
            "transcribed_text": "",
            "answer_text":      "Something went wrong. Please try again.",
            "language":         "en",
            "audio_b64":        "",
            "success":          False,
            "error":            str(e),
        }


@router.post("/tts")
async def tts_only(body: dict):
    """
    Text-to-speech only endpoint.
    Used by the frontend to speak any text (e.g. calculator results).

    Request body: { "text": "...", "language": "en" | "ur" | "roman" }
    Response:     { "audio_b64": "base64 mp3" }
    """
    text     = body.get("text", "").strip()
    language = body.get("language", "en")

    if not text:
        return {"audio_b64": ""}

    audio_bytes = text_to_speech_sync(text, language)
    return {"audio_b64": base64.b64encode(audio_bytes).decode() if audio_bytes else ""}
