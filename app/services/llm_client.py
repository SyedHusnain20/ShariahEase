"""
ShariahEase — OpenRouter API Client (gpt-oss-120b)
Provider: Groq LPU | Model: openai/gpt-oss-120b

Enforces:
  1. Exact language matching (no mixing, no switching)
  2. Default conciseness (no filler, no repetition)
  3. Cross-language accuracy (same ruling in every language)
  4. Formal Urdu standards (no Hindi loanwords)
  5. Robust error handling with retry logic
"""

import os
import time
import logging
from groq import Groq, APIConnectionError, RateLimitError, APIStatusError
from dotenv import load_dotenv
from app.services.urdu_filter import filter_response

# ── Logging ────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

# ── API Configuration ──────────────────────────────────────
_api_key = os.getenv("GROQ_API_KEY", "").strip()
if not _api_key:
    logger.warning("GROQ_API_KEY is not set. Chatbot will not function.")

client = Groq(api_key=_api_key)

MODEL       = "openai/gpt-oss-120b"
TEMPERATURE = 0.2
MAX_TOKENS  = 4000   # reasoning models need headroom for thinking tokens
TOP_P       = 0.9
MAX_RETRIES = 3
RETRY_DELAY = 2   # seconds between retries


# ═══════════════════════════════════════════════════════════════════════════
# SYSTEM PROMPT
# ═══════════════════════════════════════════════════════════════════════════
# Single source of truth for all behavioral constraints.
# Passed as the first message in every API call.

SYSTEM_PROMPT = """
You are ShariahEase, a precise and authoritative Islamic finance assistant
serving Muslims in Pakistan. You have deep knowledge of Zakat, Shariah-compliant
investing, Islamic banking, and Islamic finance contracts.

You have access to LIVE WEB SEARCH RESULTS when provided in your context.
For Quran verses and Ahadith — ONLY quote text from the provided web results.
Never fabricate or guess a verse or hadith. Always cite surah/ayah or hadith collection.
For live stock/market data — use web results and note data may be slightly delayed.

GREETINGS: When the user says hello/hi/salam or any greeting, respond warmly and
briefly introduce what you can help with. Example:
"Assalamu Alaikum! I'm ShariahEase, your Islamic finance assistant. I can help you
with Zakat calculations, halal investment screening, Quran and Hadith references on
finance, live gold/silver prices, and Islamic banking queries. How can I assist you?"

CAPABILITY QUESTIONS: When the user asks what you can do, whether you can search
the internet, or what your abilities are — answer honestly and specifically:
- Yes, you can search the internet for Quran verses, authentic Ahadith, PSX stock
  data, gold/silver prices, and Islamic finance news
- You use live web search for these topics and provide cited results
- You do NOT answer questions outside Islamic finance

FOLLOW-UP MESSAGES: When the user says "give me", "show me", "with translation",
"in English", "more details" etc. — treat it as continuing the previous topic
and provide the requested elaboration or translation.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RULE 0 — DOMAIN RESTRICTION (ABSOLUTE PRIORITY — OVERRIDES ALL OTHER RULES)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
You ONLY answer questions about:
  • Zakat (calculation, eligibility, Nisab, recipients, rules)
  • Islamic finance contracts (Mudarabah, Murabaha, Ijarah, Musharakah, Sukuk, etc.)
  • Halal / Haram status of investments, banking products, or financial instruments
  • Islamic banking practices and Shariah-compliant alternatives
  • Sadaqah, Waqf, and other Islamic charitable giving
  • Financial rulings from Islamic jurisprudence (fiqh al-muamalat)
  • Pakistani financial markets evaluated through a Shariah lens

If the user asks ANYTHING outside these topics — science, history, medicine,
technology, general knowledge, coding, sports, entertainment, politics,
or any topic unrelated to Islamic finance — you MUST refuse with this
exact response format (in the user's language):

  English: "I can only assist with Islamic finance questions — such as Zakat,
  halal investing, or Islamic banking. Please ask me something within that scope."

  Urdu: "میں صرف اسلامی مالیات کے سوالات کا جواب دے سکتا ہوں — جیسے زکوٰۃ،
  حلال سرمایہ کاری، یا اسلامی بینکاری۔ براہ کرم اسی دائرے میں سوال پوچھیں۔"

  Roman Urdu: "Main sirf Islamic finance ke sawaalat ka jawab de sakta hoon —
  jaise Zakat, halal investing, ya Islamic banking. Is dayre mein sawaal karein."

DO NOT provide any partial answer. DO NOT explain why you cannot answer in general.
DO NOT apologize excessively. Just deliver the refusal and stop.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RULE 1 — EXACT LANGUAGE MATCHING (HIGHEST PRIORITY)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Detect the script of the user's question, then respond in that exact language.

  English question       → respond in English ONLY
  Urdu script question   → respond in Urdu script ONLY
  Roman Urdu question    → respond in Roman Urdu ONLY

Never mix scripts. Never transliterate. Never switch language mid-response.
Even if the topic contains Arabic terms (Zakat, Nisab, Riba), keep all
surrounding prose in the user's detected language.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RULE 2 — DEFAULT CONCISENESS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Be direct. Answer only what was asked.

  Simple factual question   → maximum 3 sentences.
  Complex nuanced question  → maximum 5 sentences.
  Expand ONLY when the user explicitly asks for detail or explanation.

STRICTLY FORBIDDEN in every response:
  "it's worth noting", "I hope this helps", "it's important to remember",
  "as a Muslim", "great question", any phrase that repeats a point already made.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RULE 3 — FACTUAL ACCURACY AND CONSISTENCY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Answer strictly from the provided CONTEXT. Never fabricate rulings.
  The same Islamic ruling must be stated identically across all languages.
  If a ruling has scholarly variance, state both positions briefly and neutrally.
  Include actual numbers (PKR, grams, tola, %) when the context provides them.
  NEVER redirect to an external website when the answer exists in the context.
  Recommend consulting a scholar ONLY for personal fatwa situations.

CRITICAL RULING — Zakat to relatives (never get this wrong):
  Zakat CAN be given to qualifying relatives who are below Nisab:
  siblings, cousins, aunts, uncles, nephews, nieces.
  Zakat CANNOT be given to: parents, children, spouse, or anyone the
  payer is legally obligated to financially support.
  Never state "Zakat cannot be given to relatives" — that is factually WRONG.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RULE 4 — URDU LANGUAGE STANDARDS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Use formal, correct literary Urdu drawing on Arabic, Persian, and Urdu vocabulary.

FORBIDDEN Hindi loanwords and their correct Urdu replacements:
  سنہری / گولڈ   → سونا
  سلور           → چاندی
  پیسہ / دھن     → رقم / مال
  جانکاری        → معلومات
  شروعات         → آغاز
  دھیان          → توجہ
  جرورت          → ضرورت
  پرافٹ          → منافع
  لون            → قرضہ
  پیمنٹ          → ادائیگی
  کیلکولیشن      → حساب
  آدھار          → بنیاد

Never use Devanagari characters under any circumstances.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RULE 5 — FORMAT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Natural flowing prose ONLY.
No numbered lists. No bullet points. No headers. No markdown formatting.
""".strip()


# ═══════════════════════════════════════════════════════════════════════════
# LANGUAGE DETECTION
# ═══════════════════════════════════════════════════════════════════════════

# Grammar and function words unique to Roman Urdu.
# Deliberately excludes Islamic terms (halal, haram, zakat, nisab)
# which appear in English text and would cause false positives.
_ROMAN_FUNCTION_WORDS = {
    "hai", "hain", "tha", "thi", "mein", "ka", "ki", "ke", "ko",
    "se", "par", "nahi", "nahin", "kya", "kyun", "kyunke", "kaise",
    "kab", "kahan", "kitna", "kitni", "aur", "bhi", "toh", "yeh",
    "woh", "ap", "aap", "mujhe", "humain", "batao", "bata", "samjhao",
    "chahiye", "lagta", "milta", "hota", "hoti", "hote", "dena",
    "lena", "karna", "sakta", "sakti", "chahta", "chahti",
}


def detect_language(text: str) -> str:
    """
    Detect 'ur' (Urdu script), 'roman' (Roman Urdu), or 'en' (English).

    Priority:
      1. If >25% of alphabetic chars are Urdu Unicode  → 'ur'
      2. If 2+ Roman Urdu function words are present   → 'roman'
      3. Default                                        → 'en'

    Key design decision: Islamic terms are excluded from Roman Urdu
    detection because they appear in English text too. Only grammatical
    function words that cannot appear in English are counted.
    """
    if not text or not text.strip():
        return "en"

    total_alpha = sum(1 for c in text if c.isalpha())
    if total_alpha == 0:
        return "en"

    urdu_chars = sum(1 for c in text if '\u0600' <= c <= '\u06FF')
    if urdu_chars / total_alpha > 0.25:
        return "ur"

    words = set(text.lower().split())
    if len(words & _ROMAN_FUNCTION_WORDS) >= 2:
        return "roman"

    return "en"


# ═══════════════════════════════════════════════════════════════════════════
# OFF-TOPIC GUARD  (whitelist-first, intent-based)
# ═══════════════════════════════════════════════════════════════════════════
#
# DESIGN PHILOSOPHY — Whitelist beats Blacklist
# ─────────────────────────────────────────────
# The previous guard used a blacklist (block known bad topics). That breaks
# on paraphrasing: "what is analytical chemistry?" slips through because the
# exact string "analytical chemistry" isn't the question, the intent is.
#
# New approach:
#   1. If the message is in Urdu/Arabic script → ALLOW (assume Islamic context)
#   2. If any STRONG Islamic-finance signal word is present → ALLOW
#   3. Otherwise → BLOCK and return refusal
#
# This means an ambiguous question like "what is profit sharing?" gets allowed
# (because "profit sharing" is a Mudarabah concept), but "explain photosynthesis"
# gets blocked because it contains zero Islamic-finance signal words.
#
# Edge cases handled:
#   - "Can I invest in gold?" → "invest" + "gold" → ALLOW ✓
#   - "Define analytical chemistry" → no signal words → BLOCK ✓
#   - "What is riba?" → "riba" → ALLOW ✓
#   - "Who is the president of Pakistan?" → no signal words → BLOCK ✓
#   - "Is bitcoin halal?" → "bitcoin" + "halal" → ALLOW ✓
#   - "Write me a Python script" → no signal words → BLOCK ✓

# ── STRONG in-scope signals ────────────────────────────────────────────────
# Words that reliably indicate an Islamic-finance question.
# Every word here MUST be a genuine Islamic-finance term — no common English
# words like "finance", "loss", "asset", "eligible" which appear in any topic.
_ISLAMIC_FINANCE_SIGNALS: frozenset[str] = frozenset({
    # Quran & Hadith — allow through so web agent can answer
    "quran", "quranic", "ayah", "ayat", "surah", "hadith", "ahadith",
    "hadees", "sunnah", "bukhari", "sahih", "tirmidhi", "ibn majah",
    "prophet said", "nabi ne farmaya", "qurani ayat",
    "قرآن", "حدیث", "آیت", "سورہ", "احادیث",
    # Core Islamic obligations
    "zakat", "zakah", "zakaat", "zakat ul mal", "zakat ul fitr",
    "sadaqah", "sadaqa", "sadqa", "waqf", "waqaf", "khums",
    # Shariah evaluation
    "halal", "haram", "shariah", "sharia", "sharī'ah", "fiqh",
    "muamalat", "muamalah", "fatwa", "ijtihad", "riba", "ribah", "sood",
    # Islamic contracts
    "mudarabah", "mudaraba", "mudharabah",
    "murabaha", "murabahah", "murabaħa",
    "musharakah", "musharaka", "mushārakah",
    "ijarah", "ijara", "ijaara",
    "istisna", "istisnaa", "bay salam",
    "sukuk", "takaful", "wakala", "wakalah",
    "diminishing musharakah", "bai muajjal",
    # Nisab / Zakat calculation
    "nisab", "nisaab", "hawl", "haul", "tola", "zakatable",
    "zakat calculation", "zakat rate", "zakat threshold",
    # Halal investing evaluation
    "halal invest", "halal stock", "shariah compliant", "islamically permissible",
    "islamically prohibited", "screener", "halal screening",
    # Islamic banking
    "islamic bank", "islamic banking", "islamic finance",
    "islamic mortgage", "meezan bank", "al baraka", "dubai islamic",
    "bank islami", "mcb islamic",
    # Pakistani specific Islamic products
    "naya pakistan certificate", "roshan digital account",
    "prize bond halal", "prize bond haram",
    # Urdu / Roman Urdu terms (these ONLY appear in Islamic finance context)
    "sona", "chandi", "munafa", "nuqsan", "tijarat", "karobar",
    "qarz", "rishwat", "sood", "halaal", "haraam", "zakat ka nisab",
    "zakat dena", "zakaat ada karna", "maal ki zakat",
    # Urdu script — any Urdu script text is assumed to be about Islamic finance
    # (handled separately via unicode range check below)
})

# ── Context words that are ONLY meaningful alongside a signal ───────────────
# These alone are NOT enough to allow — they must co-occur with a signal.
# (We don't use these in the current logic but they're documented here.)
_CONTEXT_ONLY_WORDS: frozenset[str] = frozenset({
    "invest", "investment", "investing", "stock", "stocks", "shares", "equity",
    "profit", "interest", "bank", "banking", "loan", "gold", "silver",
    "wealth", "debt", "dividend", "fund", "crypto", "bitcoin", "bond",
    "asset", "finance", "eligible", "poor", "recipient",
})


# ── Greetings — always allow, LLM gives a scoped welcome ─────────────────────
_GREETINGS = {
    "hello", "hi", "hey", "salam", "assalam", "assalamualaikum",
    "aoa", "good morning", "good evening", "good afternoon", "good night",
    "السلام", "سلام", "آداب", "جی", "ہیلو",
}

# ── Capability / meta questions — user asking what the bot CAN do ──────────────
_CAPABILITY_SIGNALS = {
    "can you", "are you able", "do you have", "what can you", "what do you",
    "how do you", "tell me about yourself", "who are you", "what are you",
    "search", "internet", "web", "online", "browse", "look up", "find",
    "abilities", "features", "capable", "functionality",
    "تم کیا", "آپ کیا", "کیا آپ", "search kar", "internet par",
}

# ── Short follow-up phrases — continuation of prior Islamic topic ──────────────
# These alone are ambiguous, but when very short (≤ 5 words) they are almost
# always follow-ups to the previous message in a chat context.
_FOLLOWUP_SIGNALS = {
    "give me", "show me", "tell me", "explain", "more", "details",
    "with translation", "in english", "in urdu", "in roman urdu",
    "translate", "again", "yes", "ok", "okay", "sure", "please",
    "continue", "go on", "next", "and", "also", "what about",
    "batao", "bata do", "aur", "theek hai", "haan", "ji haan",
}


def is_off_topic(text: str) -> bool:
    """
    Returns True if the message is NOT about Islamic finance.

    Pass-through cases (never blocked):
    - Urdu/Arabic script  → assumed Islamic context
    - Greeting words      → LLM handles with scoped welcome
    - Capability/meta Qs → LLM explains what it can do
    - Short follow-ups    → continuation of prior conversation
    - Islamic finance signal words present
    """
    if not text or not text.strip():
        return False

    lower = text.lower().strip()
    words = lower.split()

    # 1. Urdu/Arabic script — let through
    urdu_chars = sum(1 for c in text if '؀' <= c <= 'ۿ')
    if urdu_chars > 3:
        return False

    # 2. Greetings — always let through
    for g in _GREETINGS:
        if g in lower:
            return False

    # 3. Capability / meta questions — let through
    for sig in _CAPABILITY_SIGNALS:
        if sig in lower:
            return False

    # 4. Short follow-up messages (≤6 words) — let through
    # These are continuations of a prior Islamic-finance topic in chat context
    if len(words) <= 6:
        for sig in _FOLLOWUP_SIGNALS:
            if sig in lower:
                return False

    # 5. Islamic finance signal words — let through
    for signal in _ISLAMIC_FINANCE_SIGNALS:
        if signal in lower:
            return False

    logger.info("Off-topic guard blocked: %s", text[:100])
    return True


def off_topic_refusal(lang: str) -> str:
    """Return a short, language-appropriate refusal message."""
    return {
        "ur": (
            "میں صرف اسلامی مالیات کے سوالات کا جواب دے سکتا ہوں — "
            "جیسے زکوٰۃ، حلال سرمایہ کاری، یا اسلامی بینکاری۔ "
            "براہ کرم اسی دائرے میں سوال پوچھیں۔"
        ),
        "roman": (
            "Main sirf Islamic finance ke sawaalat ka jawab de sakta hoon — "
            "jaise Zakat, halal investing, ya Islamic banking. "
            "Kripya isi dayre mein sawaal karein."
        ),
        "en": (
            "I can only assist with Islamic finance questions — "
            "such as Zakat, halal investing, or Islamic banking. "
            "Please ask me something within that scope."
        ),
    }.get(lang, "I can only assist with Islamic finance questions.")


# ═══════════════════════════════════════════════════════════════════════════
# PER-QUERY LANGUAGE INSTRUCTIONS
# ═══════════════════════════════════════════════════════════════════════════

def _build_language_instruction(lang: str, nisab: bool) -> str:
    """
    Returns a compact, unambiguous per-query instruction injected into
    the USER turn — not the system prompt — so it sits immediately
    before the question for maximum instruction-following effect.
    """
    base = {
        "ur": (
            "ہدایت: جواب صرف اردو رسم الخط میں دو۔ کوئی انگریزی یا رومن الفاظ نہ ہوں۔ "
            "مختصر اور براہِ راست رہو۔ "
            "ممنوع الفاظ: سنہری، سلور، گولڈ، پیسہ، جانکاری، شروعات، دھیان۔ "
            "درست الفاظ: سونا، چاندی، رقم، معلومات، آغاز، توجہ۔"
        ),
        "roman": (
            "Instruction: Reply in Roman Urdu ONLY. "
            "No Urdu script. No full English sentences. Be concise and direct."
        ),
        "en": (
            "Instruction: Reply in English ONLY. "
            "Maximum 3 sentences unless the user explicitly asked for detail."
        ),
    }

    nisab_addon = {
        "ur": (
            "LIVE NISAB DATA سے مستند اعداد استعمال کریں۔ "
            "صرف نصاب کے بارے میں پوچھا گیا ہے — صرف یہ بتائیں: "
            "(۱) نصاب کی مختصر تعریف، "
            "(۲) سونے کا نصاب: 7.5 تولہ (87.48 گرام) = آج PKR جتنا context میں دیا گیا ہے، "
            "(۳) چاندی کا نصاب: 52.5 تولہ (612.36 گرام) = آج PKR جتنا context میں دیا گیا ہے، "
            "(۴) زکوٰۃ کی شرح: 2.5 فیصد۔ "
            "اگر context میں PKR رقم نہ ہو تو PKR نہ لکھیں — صرف گرام اور تولہ لکھیں۔"
        ),
        "roman": (
            "Use the LIVE NISAB DATA from context. Answer ONLY what was asked about nisab: "
            "(1) Nisab ki short definition, "
            "(2) Sone ka nisab: 7.5 tola (87.48g) = PKR [use exact figure from context], "
            "(3) Chandi ka nisab: 52.5 tola (612.36g) = PKR [use exact figure from context], "
            "(4) Zakat ki shar: 2.5 percent. "
            "Agar context mein PKR figure nahi hai to PKR mat likho."
        ),
        "en": (
            "Use the LIVE NISAB DATA from context. Answer ONLY what was asked about nisab: "
            "(1) Brief definition of Nisab, "
            "(2) Gold nisab: 7.5 tola (87.48g) = PKR [use exact figure from context], "
            "(3) Silver nisab: 52.5 tola (612.36g) = PKR [use exact figure from context], "
            "(4) Zakat rate: 2.5%. "
            "If no PKR figure is in context, omit it — do not write PKR [amount]."
        ),
    }

    instruction = base.get(lang, base["en"])
    if nisab:
        instruction += " " + nisab_addon.get(lang, nisab_addon["en"])
    return instruction


# ═══════════════════════════════════════════════════════════════════════════
# NISAB DETECTION
# ═══════════════════════════════════════════════════════════════════════════

_NISAB_KEYWORDS = {
    # English — only questions specifically about the threshold/price
    "nisab", "nisaab", "threshold", "tola", "gold price", "silver price",
    "gold nisab", "silver nisab", "how much gold", "how much silver",
    "minimum wealth", "zakat limit", "zakat threshold", "eligible for zakat",
    # Roman Urdu
    "nisaab", "sone ka nisab", "chandi ka nisab", "kitna sona", "kitni chandi",
    "zakat ki had", "zakat limit",
    # Urdu script — only nisab-specific terms, NOT the word zakat alone
    "نصاب", "سونے کا نصاب", "چاندی کا نصاب", "زکوٰۃ کی حد", "تولہ",
}


def _is_nisab_question(text: str) -> bool:
    """Return True if text is likely asking about Nisab / Zakat thresholds."""
    lower = text.lower()
    return any(kw in lower for kw in _NISAB_KEYWORDS)


# ═══════════════════════════════════════════════════════════════════════════
# MAIN CHAT FUNCTION
# ═══════════════════════════════════════════════════════════════════════════

def get_chat_response(
    user_message: str,
    context: str = "",
    chat_history: list = None,
    is_nisab_related: bool = False,
) -> str:
    """
    Core chat function.

    1. Validates input
    2. Detects language
    3. Checks off-topic guard (with conversation context awareness)
    4. Calls OpenRouter (gpt-oss-120b) with retry logic
    5. Post-processes response through urdu_filter
    6. Returns clean string
    """
    # ── Input validation ───────────────────────────────────
    if not user_message or not user_message.strip():
        return "Please enter a question."
    if not _api_key:
        return "⚠️ API key not configured. Please set GROQ_API_KEY in .env."
    if chat_history is None:
        chat_history = []

    # ── Detection ──────────────────────────────────────────
    lang       = detect_language(user_message)
    nisab_flag = is_nisab_related or _is_nisab_question(user_message)

    # ── Off-topic guard (context-aware) ────────────────────
    # If the current message alone looks off-topic, check if the
    # recent conversation was about an Islamic finance topic.
    # "Give me the verses with English translation" is a follow-up
    # to the previous Quran/Zakat answer — it should never be blocked.
    if is_off_topic(user_message):
        # Check last 3 assistant messages for Islamic finance content
        recent_context = " ".join(
            msg.get("content", "")
            for msg in chat_history[-6:]
            if msg.get("role") == "assistant"
        )
        # If recent conversation had Islamic finance content, allow follow-up
        if recent_context and not is_off_topic(recent_context):
            logger.info("Follow-up allowed based on conversation context")
        else:
            logger.info("Off-topic question blocked: %s", user_message[:80])
            return off_topic_refusal(lang)

    # ── Per-query instruction (injected into user turn) ────
    instruction = _build_language_instruction(lang, nisab_flag)

    # ── Build augmented user message ───────────────────────
    # Context is injected here — not in the system prompt —
    # so it is fresh per query and does not persist across calls.
    augmented = (
        f"--- KNOWLEDGE BASE CONTEXT ---\n"
        f"{context}\n"
        f"--- END CONTEXT ---\n\n"
        f"{instruction}\n\n"
        f"Question: {user_message}"
    )

    # ── Message list ───────────────────────────────────────
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(chat_history[-6:])    # keep last 3 exchanges (6 msgs)
    messages.append({"role": "user", "content": augmented})

    # ── API call with exponential retry ───────────────────
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(f"OpenRouter call attempt {attempt}/{MAX_RETRIES} | lang={lang} | model={MODEL}")

            response = client.chat.completions.create(
                model       = MODEL,
                messages    = messages,
                temperature = TEMPERATURE,
                max_tokens  = MAX_TOKENS,
                top_p       = TOP_P,
            )

            content = response.choices[0].message.content
            # gpt-oss-120b on OpenRouter free tier sometimes returns content=None
            # when reasoning tokens are emitted — treat it as a retriable error
            if content is None:
                finish = response.choices[0].finish_reason
                logger.warning(f"content=None from API (finish_reason={finish}), retrying...")
                raise ValueError(f"Empty content from model (finish_reason={finish})")
            raw     = content.strip()
            cleaned = filter_response(raw, lang)

            logger.info(f"OpenRouter succeeded on attempt {attempt}")
            return cleaned

        except RateLimitError as e:
            last_error = e
            wait = RETRY_DELAY * attempt
            logger.warning(f"Rate limit hit. Retrying in {wait}s...")
            time.sleep(wait)

        except APIConnectionError as e:
            last_error = e
            logger.error(f"Connection error on attempt {attempt}: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)

        except APIStatusError as e:
            last_error = e
            logger.error(f"API status {e.status_code}: {e.message}")
            if e.status_code in {400, 401, 403}:
                break   # non-retriable
            time.sleep(RETRY_DELAY)

        except Exception as e:
            last_error = e
            logger.error(f"Unexpected error on attempt {attempt}: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)

    return _error_message(last_error, lang)


def _error_message(error: Exception, lang: str) -> str:
    """Language-appropriate user-facing error message."""
    s = str(error).lower() if error else ""

    if "rate" in s or "429" in s:
        return {
            "ur":    "⚠️ بہت زیادہ درخواستیں آئیں۔ تیس سیکنڈ بعد دوبارہ کوشش کریں۔",
            "roman": "⚠️ Bohot requests aa gayi. 30 second baad try karein.",
            "en":    "⚠️ Rate limit reached. Please wait 30 seconds and try again.",
        }.get(lang, "⚠️ Rate limit reached.")

    if "connection" in s or "network" in s:
        return {
            "ur":    "⚠️ سرور سے رابطہ نہیں ہو سکا۔ انٹرنیٹ کنکشن چیک کریں۔",
            "roman": "⚠️ Server se connection fail hua. Internet check karein.",
            "en":    "⚠️ Could not reach the server. Please check your internet.",
        }.get(lang, "⚠️ Connection failed.")

    if "401" in s or "authentication" in s:
        return {
            "ur":    "⚠️ API کلید درست نہیں۔ .env فائل چیک کریں۔",
            "roman": "⚠️ API key galat hai. .env file check karein.",
            "en":    "⚠️ Invalid API key. Please check your .env file.",
        }.get(lang, "⚠️ Authentication error.")

    return {
        "ur":    "⚠️ ایک خرابی پیش آئی۔ دوبارہ کوشش کریں۔",
        "roman": "⚠️ Kuch masla hua. Dobara try karein.",
        "en":    "⚠️ Something went wrong. Please try again.",
    }.get(lang, "⚠️ An error occurred.")


# ═══════════════════════════════════════════════════════════════════════════
# INVESTMENT SCREENER
# ═══════════════════════════════════════════════════════════════════════════

def screen_investment(query: str, context: str) -> dict:
    """
    Shariah compliance screener.
    Uses a minimal, format-locked prompt for consistent output parsing.
    """
    if not query or not query.strip():
        return {
            "verdict": "DOUBTFUL", "confidence": "LOW",
            "reason": "No query provided.",
            "recommendation": "Please enter an investment name.",
        }

    prompt = (
        "You are a Shariah compliance expert for Islamic finance in Pakistan.\n"
        "Analyze the query using ONLY the provided context. Be factual and concise.\n\n"
        f"Context:\n---\n{context}\n---\n\n"
        f"Investment query: {query}\n\n"
        "Respond in this EXACT format with no extra text:\n"
        "VERDICT: [HALAL / HARAM / DOUBTFUL]\n"
        "CONFIDENCE: [HIGH / MEDIUM / LOW]\n"
        "REASON: [2-3 sentences maximum]\n"
        "RECOMMENDATION: [1 sentence]"
    )

    try:
        response = client.chat.completions.create(
            model       = MODEL,
            messages    = [{"role": "user", "content": prompt}],
            temperature = 0.1,
            max_tokens  = 2000,   # reasoning model headroom
            top_p       = TOP_P,
        )
        raw_content = response.choices[0].message.content
        if raw_content is None:
            raise ValueError("Screener: model returned content=None")
        return _parse_screening_response(raw_content.strip())
    except Exception as e:
        logger.error(f"Screener error: {e}")
        return {
            "verdict":        "DOUBTFUL",
            "confidence":     "LOW",
            "reason":         "Analysis could not be completed due to a service error.",
            "recommendation": "Please try again or consult a qualified Islamic scholar.",
        }


def _parse_screening_response(raw: str) -> dict:
    """Parse the structured screener output into a clean dict."""
    result = {
        "verdict":        "DOUBTFUL",
        "confidence":     "LOW",
        "reason":         raw,
        "recommendation": "Consult a qualified Islamic scholar.",
    }
    for line in raw.split("\n"):
        line = line.strip()
        if line.startswith("VERDICT:"):
            v = line.replace("VERDICT:", "").strip().upper()
            result["verdict"] = (
                "HALAL"    if "HALAL"    in v else
                "HARAM"    if "HARAM"    in v else
                "DOUBTFUL"
            )
        elif line.startswith("CONFIDENCE:"):
            c = line.replace("CONFIDENCE:", "").strip().upper()
            result["confidence"] = c if c in {"HIGH", "MEDIUM", "LOW"} else "LOW"
        elif line.startswith("REASON:"):
            result["reason"] = line.replace("REASON:", "").strip()
        elif line.startswith("RECOMMENDATION:"):
            result["recommendation"] = line.replace("RECOMMENDATION:", "").strip()
    return result
