import os
import logging
import httpx
from groq import AsyncGroq

logger = logging.getLogger(__name__)

# ── Groq client ───────────────────────────────────────────────────────────────
# Read at call time — so rotating env vars doesn't require a server restart
def _groq():
    return AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))

def _wa_url():
    pid = os.getenv("PHONE_NUMBER_ID", "")
    return f"https://graph.facebook.com/v19.0/{pid}/messages"

def _headers():
    return {
        "Authorization": f"Bearer {os.getenv('WHATSAPP_TOKEN', '')}",
        "Content-Type": "application/json",
    }

# ── Per-phone conversation history (in-memory, max 10 turns) ─────────────────
# Format: { "923001234567": [{"role": "user"|"assistant", "content": "..."}] }
_HISTORY: dict[str, list] = {}
_MAX_HISTORY_TURNS = 10   # keep last 10 exchanges = 20 messages

# ── Off-topic guard ───────────────────────────────────────────────────────────
_ALLOWED_TOPICS = {
    # Islamic finance
    "zakat", "zakah", "riba", "interest", "halal", "haram", "sukuk",
    "murabaha", "mudarabah", "musharakah", "ijarah", "wakala", "takaful",
    "nisab", "gold", "silver", "investment", "stock", "screener", "shariah",
    "islamic", "finance", "bank", "banking", "loan", "debt", "charity",
    "sadaqah", "waqf", "fatwa", "fiqh", "quran", "hadith", "sunnah",
    "prophet", "allah", "islam", "muslim", "profit", "loss", "trade",
    # Urdu / Roman Urdu signals
    "زکوٰة", "حلال", "حرام", "سود", "ربا", "نصاب", "صدقہ",
    "zakat kya", "halal hai", "haram hai", "islamic", "sharia",
    "maal", "sona", "chandi", "munafa", "tijarat", "qarz",
}

_GREETING_RESPONSES = {
    "hi", "hello", "salam", "assalam", "hey", "helo", "slm", "aoa",
    "assalamualaikum", "assalam o alaikum", "walaikum",
}

MAX_INPUT_CHARS = 1000

# ── System prompt (full — matches web chatbot quality) ───────────────────────
SYSTEM_PROMPT = """\
You are ShariahEase AI — an expert Islamic finance assistant built for Muslims in Pakistan and globally.

EXPERTISE:
- Zakat rules, Nisab calculation, eligible recipients (8 asnaf)
- Halal vs Haram investments: stocks, mutual funds, crypto, real estate
- Riba (interest) — identification and avoidance
- Islamic finance contracts: Murabaha, Mudarabah, Musharakah, Ijarah, Sukuk, Takaful
- PSX KMI Shariah-compliant stocks
- Sadaqah, Waqf, and Islamic charity

LANGUAGE:
- Detect the user's language automatically
- Reply in the same language: English, Urdu, or Roman Urdu
- For Urdu: use clear, simple script

CITING SOURCES:
- Cite Quran verses (Surah:Ayah) and Hadith (collection + number) when relevant
- Never fabricate citations — only cite what you know with confidence

RESPONSE STYLE:
- WhatsApp format: use *bold* for emphasis, keep paragraphs short
- Be concise — 3–5 sentences for simple questions, more detail for complex ones
- Always recommend consulting a qualified scholar for personal fatwas

BOUNDARIES:
- Only answer Islamic finance and economics questions
- Politely decline anything unrelated (general AI assistant, coding, politics, etc.)
- Never give investment advice as a personal recommendation — always add a disclaimer
"""


def _is_allowed(text: str) -> bool:
    """
    Returns True if the message touches an Islamic finance topic.
    Greetings pass through. Pure off-topic messages are blocked.
    """
    lower = text.lower().strip()

    # Always allow greetings
    if any(g in lower for g in _GREETING_RESPONSES):
        return True

    # Allow if any Islamic finance keyword found
    if any(kw in lower for kw in _ALLOWED_TOPICS):
        return True

    # Allow Arabic/Urdu script (any Arabic Unicode block character)
    if any("\u0600" <= c <= "\u06FF" for c in text):
        return True

    return False


def _trim_history(phone: str):
    """Keep only the last _MAX_HISTORY_TURNS exchanges."""
    history = _HISTORY.get(phone, [])
    if len(history) > _MAX_HISTORY_TURNS * 2:
        _HISTORY[phone] = history[-((_MAX_HISTORY_TURNS * 2)):]


async def send_text(to: str, text: str):
    """Send a WhatsApp text message. Logs errors instead of swallowing them."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(
                _wa_url(),
                headers=_headers(),
                json={
                    "messaging_product": "whatsapp",
                    "to": to,
                    "type": "text",
                    "text": {"body": text},
                },
            )
            if res.status_code != 200:
                logger.error(
                    "WhatsApp send failed: status=%s body=%s",
                    res.status_code, res.text[:200],
                )
    except Exception as e:
        logger.error("WhatsApp send_text exception: %s", e)


async def get_reply(phone: str, user_input: str) -> str:
    """Build conversation history and get LLM reply."""
    history = _HISTORY.setdefault(phone, [])

    # Append user turn
    history.append({"role": "user", "content": user_input})

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history

    try:
        response = await _groq().chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            temperature=0.4,
            max_tokens=512,
        )
        reply = response.choices[0].message.content or ""
    except Exception as e:
        logger.error("Groq error in WhatsApp handler: %s", e)
        reply = "Sorry, I'm having trouble responding right now. Please try again in a moment."

    # Append assistant turn and trim
    history.append({"role": "assistant", "content": reply})
    _trim_history(phone)

    return reply


async def handle_message(sender: str, message_type: str, payload: dict):
    """
    Main entry point called by the webhook for every incoming message.

    message_type: the value of msg["type"] from Meta's payload
    payload:      the full msg dict
    """

    # ── Handle non-text message types gracefully ──────────────────────────────
    if message_type != "text":
        type_replies = {
            "image":    "JazakAllah Khair for sharing! I can only process text messages. Please type your Islamic finance question.",
            "audio":    "JazakAllah Khair! Voice messages aren't supported yet. Please type your question.",
            "video":    "JazakAllah Khair! I can only read text. Please type your Islamic finance question.",
            "sticker":  "😊 Please type your Islamic finance question and I'll be happy to help!",
            "location": "I don't process locations. Please type your Islamic finance question.",
            "document": "JazakAllah Khair for sharing! I can only process text questions right now.",
            "reaction": None,  # reactions — silently ignore, no reply
        }
        reply_text = type_replies.get(message_type)
        if reply_text:
            await send_text(sender, reply_text)
        return

    # ── Extract and validate text ─────────────────────────────────────────────
    text = payload.get("text", {}).get("body", "").strip()

    if not text:
        return

    # Input length cap — prevent Groq quota abuse
    if len(text) > MAX_INPUT_CHARS:
        await send_text(
            sender,
            f"Your message is too long ({len(text)} characters). "
            f"Please keep it under {MAX_INPUT_CHARS} characters and try again."
        )
        return

    # Off-topic guard
    if not _is_allowed(text):
        await send_text(
            sender,
            "Assalam o Alaikum! 🌙 I'm ShariahEase — an Islamic finance assistant.\n\n"
            "I can help you with:\n"
            "• Zakat calculation & rules\n"
            "• Halal vs Haram investments\n"
            "• Riba (interest) questions\n"
            "• Islamic finance contracts\n\n"
            "Please ask me an Islamic finance question!"
        )
        return

    # ── Get and send reply ────────────────────────────────────────────────────
    reply = await get_reply(sender, text)
    await send_text(sender, reply)
