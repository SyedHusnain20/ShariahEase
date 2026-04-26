"""
ShariahEase — Groq API Client
Production-ready rewrite with strict prompt engineering.

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

MODEL       = "llama-3.3-70b-versatile"
TEMPERATURE = 0.2
MAX_TOKENS  = 350
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
       "ur ": (
    "LIVE NISAB DATA سے مستند اعداد استعمال کریں۔ "
    "مکمل، رواں جملوں میں لکھیں: "
    "(۱) نصاب کی مختصر تعریف، "
    "(۲) سونے کا نصاب: 7.5 تولہ (87.48 گرام) = آج کی قیمت میں PKR [رقم]، "
    "(۳) چاندی کا نصاب: 52.5 تولہ (612.36 گرام) = آج کی قیمت میں PKR [رقم]، "
    "(۴) زکوٰۃ کی شرح: 2.5 فیصد، "
    "(۵) ایک جملہ کہ یہ اقدار آج کی مارکیٹ قیمتوں پر مبنی ہیں اور روزانہ تبدیل ہو سکتی ہیں۔ "
    "گرامر ہدایات: 'وہ' کا تکرار ہرگز نہ کریں، مفعول کو فعل کے قریب رکھیں، 'یہ/اس' کا مرجع ہمیشہ واضح رکھیں۔ "
    "'وزارت' یا 'ویب سائٹ' کا ذکر ہرگز نہ کریں۔ "
),
        "roman": (
            " LIVE NISAB DATA se exact PKR figures lo. Roman Urdu prose mein:"
            " nisab ki definition, sone ka nisab (7.5 tola = PKR [amount] aaj),"
            " chandi ka nisab (52.5 tola = PKR [amount] aaj), 2.5% rate,"
            " ek line ke prices change hoti hain. Kisi website ka zikar mat karo."
        ),
        "en": (
            " Use exact PKR figures from LIVE NISAB DATA. Natural prose:"
            " define Nisab, then Gold Nisab (7.5 tola = PKR today),"
            " Silver Nisab (52.5 tola = PKR today), Zakat rate 2.5%,"
            " one sentence that prices change daily. Do not mention any website."
        ),
    }

    instruction = base.get(lang, base["en"])
    if nisab:
        instruction += nisab_addon.get(lang, nisab_addon["en"])
    return instruction


# ═══════════════════════════════════════════════════════════════════════════
# NISAB KEYWORD DETECTION
# ═══════════════════════════════════════════════════════════════════════════

_NISAB_KEYWORDS = {
    "nisab", "نصاب", "nisaab", "sona", "chandi", "سونا", "چاندی",
    "gold nisab", "silver nisab", "تولہ", "گرام", "tola", "gram",
    "how much zakat", "zakat amount", "زکوٰۃ کتنی", "کتنا نصاب",
    "zakat rate", "2.5",
}


def _is_nisab_question(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in _NISAB_KEYWORDS)


# ═══════════════════════════════════════════════════════════════════════════
# MAIN CHAT FUNCTION
# ═══════════════════════════════════════════════════════════════════════════

def ask_groq(
    user_message:     str,
    context:          str,
    chat_history:     list = None,
    is_nisab_related: bool = False,
) -> str:
    """
    Core chat function.

    1. Validates input
    2. Detects language
    3. Builds instruction
    4. Calls Groq with retry logic
    5. Post-processes response through urdu_filter
    6. Returns clean string

    Args:
        user_message:     Raw user input text.
        context:          RAG chunks + live Nisab data from the router.
        chat_history:     List of previous {role, content} dicts.
        is_nisab_related: Pre-computed flag from the router.

    Returns:
        Clean response string in the user's exact language.
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
            logger.info(f"Groq call attempt {attempt}/{MAX_RETRIES} | lang={lang}")

            response = client.chat.completions.create(
                model       = MODEL,
                messages    = messages,
                temperature = TEMPERATURE,
                max_tokens  = MAX_TOKENS,
                top_p       = TOP_P,
            )

            raw     = response.choices[0].message.content.strip()
            cleaned = filter_response(raw, lang)

            logger.info(f"Groq succeeded on attempt {attempt}")
            return cleaned

        except RateLimitError as e:
            last_error = e
            wait = RETRY_DELAY * attempt
            logger.warning(f"Rate limit. Retrying in {wait}s...")
            time.sleep(wait)

        except APIConnectionError as e:
            last_error = e
            logger.error(f"Connection error attempt {attempt}: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)

        except APIStatusError as e:
            last_error = e
            logger.error(f"API {e.status_code}: {e.message}")
            if e.status_code in {400, 401, 403}:
                break   # non-retriable
            time.sleep(RETRY_DELAY)

        except Exception as e:
            last_error = e
            logger.error(f"Unexpected error attempt {attempt}: {e}")
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
            max_tokens  = 250,
            top_p       = TOP_P,
        )
        return _parse_screening_response(
            response.choices[0].message.content.strip()
        )
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
