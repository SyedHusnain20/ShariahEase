from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.database.crud import save_message, get_chat_history
from app.models.schemas import ChatRequest, ChatResponse
from app.services.rag_service import rag_service
from app.services.llm_client import get_chat_response
from app.services.metal_price import get_nisab_values

router = APIRouter(prefix="/chat", tags=["Chatbot"])

NISAB_KEYWORDS = [
    "نصاب", "nisab", "سونا", "چاندی", "gold", "silver",
    "زکوٰۃ کی حد", "zakat limit", "sona", "chandi",
    "تولہ", "گرام", "tola", "gram",
]


def is_nisab_question(text: str) -> bool:
    t = text.lower()
    return any(k.lower() in t for k in NISAB_KEYWORDS)


async def get_live_nisab_block() -> tuple[str, dict]:
    """Returns (formatted context string, raw data dict)"""
    try:
        data         = await get_nisab_values()
        gold_nisab   = int(data["gold_nisab_pkr"])
        silver_nisab = int(data["silver_nisab_pkr"])
        gold_gram    = int(data["gold_per_gram_pkr"])
        silver_gram  = int(data["silver_per_gram_pkr"])

        block = f"""
=== CURRENT LIVE NISAB DATA — USE THESE EXACT NUMBERS IN YOUR ANSWER ===
Gold price today     : PKR {gold_gram:,} per gram
Silver price today   : PKR {silver_gram:,} per gram

Gold Nisab  (7.5 tola = 87.48 grams) : PKR {gold_nisab:,}
Silver Nisab (52.5 tola = 612.36 grams): PKR {silver_nisab:,}

Zakat rate  : 2.5% of all zakatable wealth above Nisab
Data source : {data.get("source", "fallback")}

INSTRUCTION: When answering about Nisab, structure your answer as:
1. Brief definition of Nisab (1-2 sentences)
2. Gold Nisab: 7.5 tola = 87.48 grams = PKR {gold_nisab:,} TODAY
3. Silver Nisab: 52.5 tola = 612.36 grams = PKR {silver_nisab:,} TODAY
4. Zakat rate: 2.5%
5. One-line disclaimer about prices changing daily
DO NOT say "check another website" — all data is provided above.
=== END LIVE NISAB DATA ===
""".strip()
        return block, data

    except Exception:
        block = """
=== NISAB DATA (APPROXIMATE — LIVE PRICE UNAVAILABLE) ===
Gold Nisab  (7.5 tola = 87.48 grams) : approximately PKR 18,00,000 to PKR 20,00,000
Silver Nisab (52.5 tola = 612.36 grams): approximately PKR 1,50,000 to PKR 2,00,000
Zakat rate: 2.5%
Note: Live prices temporarily unavailable. Figures are approximate.
=== END NISAB DATA ===
""".strip()
        return block, {}


@router.post("/message", response_model=ChatResponse)
async def chat_message(request: ChatRequest, db: Session = Depends(get_db)):

    save_message(db, request.session_id, "user", request.message)

    history_records = get_chat_history(db, request.session_id, limit=6)
    chat_history = [
        {"role": r.role, "content": r.content}
        for r in history_records[:-1]
    ]

    rag_context = rag_service.build_context(request.message, top_k=5)

    # Always inject live Nisab — but emphasise it when question is about Nisab/gold
    nisab_block, _ = await get_live_nisab_block()
    full_context   = f"{nisab_block}\n\n{rag_context}"

    sources = rag_service.search(request.message, top_k=3)
    source_names = list({
        s["source"].replace("_", " ").replace(".txt", "").title()
        for s in sources
    })

    if not rag_service.is_ready:
        answer = "Knowledge base not loaded. Please run: python knowledge_base/build_index.py"
    else:
        try:
            answer = get_chat_response(
                user_message     = request.message,
                context          = full_context,
                chat_history     = chat_history,
                is_nisab_related = is_nisab_question(request.message),
            )
        except Exception as e:
            err = str(e).lower()
            if "connection" in err or "network" in err:
                answer = "⚠️ Could not reach the AI server. Please check your internet connection."
            elif "401" in err or "authentication" in err:
                answer = "⚠️ API key is invalid. Please check your .env file."
            elif "429" in err or "rate_limit" in err:
                answer = "⚠️ Too many requests. Please wait 30 seconds and try again."
            else:
                answer = "⚠️ AI service error. Please try again in a moment."

    save_message(db, request.session_id, "assistant", answer)

    return ChatResponse(
        session_id = request.session_id,
        answer     = answer,
        language   = "auto",
        sources    = source_names,
    )


@router.get("/history/{session_id}")
async def get_history(session_id: str, db: Session = Depends(get_db)):
    records = get_chat_history(db, session_id, limit=50)
    return [
        {"role": r.role, "content": r.content,
         "created_at": r.created_at.strftime("%I:%M %p")}
        for r in records
    ]


@router.delete("/history/{session_id}")
async def clear_history(session_id: str, db: Session = Depends(get_db)):
    from app.database.models import ChatMessage
    db.query(ChatMessage).filter(
        ChatMessage.session_id == session_id
        ).delete()
    db.commit()
    return {"cleared": True}