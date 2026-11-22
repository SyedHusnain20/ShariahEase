"""
The AI expense assistant (Path step 4b): what happens to a chat message from a logged-in person BEFORE the normal
chatbot (and its topic guard) sees it. Called from app/routers/chatbot.py.

    handle(...) returns None   -> "not mine": the normal chatbot answers, exactly as before
    handle(...) returns Handled -> this is the reply (and, for an expense, a confirmation card)

Order of events for one message (every step can stop it):
  0. kill switch (EXPENSE_AI_ENABLED)                                   -> None
  1. a bare "yes" / "no" while a card is waiting                        -> confirm / cancel it (no model, no allowance)
  2. cheap pre-check: no digit, no amount word, no expense word         -> None (no model, no allowance)
  3. the same chat message sent twice                                   -> the same card again (no model, no allowance)
  4. daily token breaker open                                           -> honest "resting today" message
  5. monthly allowance (one atomic statement)                           -> honest "used up" message
  6. the understand call (strict JSON, no tools)                        -> failure: allowance given back, honest message
  7. the intent: other topics go back to the normal chatbot (allowance given back); questions about or changes of existing
     entries are not available in chat yet (Path step 5): honest message, allowance given back
  8. an expense: every amount is cross-checked against the message and normalised by code, dates and categories are
     validated; ONE problem anywhere means NO card at all (all or nothing), allowance given back
  9. a card is stored; only the person's "yes" writes the expense

Replies are fixed templates in English, Urdu and Roman Urdu: there is no free model text on this path. Nothing the person
wrote is logged. The message is not saved to the chat history, not read aloud (text-to-speech), and not traced.
Urdu text below is marked for native-speaker review.
"""
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Optional

from app.services import ai_limits, expense_service as es, pending_actions as pa
from app.services.expense_normalise import (
    DateError, _MULTIPLIERS, _WORD_VALUES, amount_in_message, fold, normalise_amount, parse_date_text,
)
from app.services.expense_understand import clean_message, taxonomy, understand
from app.services.llm_client import detect_language
from app.services.money import MoneyError

log = logging.getLogger(__name__)

_MSG_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")

# ── cheap pre-check ───────────────────────────────────────────────────────────────────────────────
# Words that make a message worth reading. Wrong "yes" costs one model call (the allowance is given back when the message
# turns out not to be an expense); wrong "no" sends an expense message to the normal chatbot, which is harmless.
_AMBIGUOUS = frozenset({"do", "ek", "one", "two", "ten", "che", "chay", "ایک", "دو"})
_AMOUNT_WORDS = (frozenset(_WORD_VALUES) | frozenset(_MULTIPLIERS)) - _AMBIGUOUS - {"k"}
_EXPENSE_WORDS = frozenset({
    "spent", "spend", "spending", "paid", "pay", "bought", "expense", "expenses", "kharcha", "kharch", "kharche", "kharchay",
    "diya", "diye", "liya", "kitna", "kitne", "total", "delete", "remove", "undo", "entry", "edit",
    "خرچ", "خرچہ", "کتنا", "کتنے", "ادا", "دیا", "دیے", "لیا", "حذف", "ہٹا", "اندراج",
})
_WORDS = re.compile(r"[\w؀-ۿ]+", re.UNICODE)

_YES = frozenset({"yes", "y", "confirm", "save", "yes save", "haan", "han", "haan save", "ہاں", "جی ہاں", "ہاں محفوظ کریں"})
_NO = frozenset({"no", "n", "cancel", "nahi", "nahin", "نہیں", "منسوخ", "cancel karo"})


def looks_like_expense_talk(text: str) -> bool:
    folded = fold(text)
    if any(ch.isdigit() for ch in folded):
        return True
    return any(w in _AMOUNT_WORDS or w in _EXPENSE_WORDS for w in _WORDS.findall(folded))


def yes_or_no(text: str) -> Optional[str]:
    word = fold(text).strip(" .!،۔؟?")
    if word in _YES:
        return "yes"
    if word in _NO:
        return "no"
    return None


# ── templates ─────────────────────────────────────────────────────────────────────────────────────
# {kind: {lang: text}}. Placeholders: {items} {n} {limit}. Urdu: needs native-speaker review.
T = {
    "card": {
        "en": "I understood:\n{items}\nReply \"yes\" to save, or \"no\" to cancel. This stays open for 15 minutes.",
        "roman_ur": "Maine yeh samjha:\n{items}\nSave karne ke liye \"haan\" likhein, cancel ke liye \"nahi\". Yeh 15 minute tak khula hai.",
        "ur": "میں نے یہ سمجھا:\n{items}\nمحفوظ کرنے کے لیے \"ہاں\" لکھیں، منسوخ کرنے کے لیے \"نہیں\"۔ یہ 15 منٹ تک کھلا ہے۔",
    },
    "saved": {
        "en": "Saved:\n{items}\nYou can change or delete it on the Expenses page.",
        "roman_ur": "Save ho gaya:\n{items}\nAap Expenses page par isay badal ya delete kar sakte hain.",
        "ur": "محفوظ ہو گیا:\n{items}\nآپ اخراجات کے صفحے پر اسے تبدیل یا حذف کر سکتے ہیں۔",
    },
    "cancelled": {
        "en": "Cancelled. Nothing was saved.",
        "roman_ur": "Cancel ho gaya. Kuch save nahi hua.",
        "ur": "منسوخ کر دیا۔ کچھ محفوظ نہیں ہوا۔",
    },
    "expired": {
        "en": "That confirmation has expired, so nothing was saved. Please send your message again.",
        "roman_ur": "Yeh confirmation khatam ho chuki hai, is liye kuch save nahi hua. Barah-e-karam dobara message bhejein.",
        "ur": "یہ تصدیق ختم ہو چکی ہے، اس لیے کچھ محفوظ نہیں ہوا۔ براہ کرم اپنا پیغام دوبارہ بھیجیں۔",
    },
    "used": {
        "en": "That one was already handled.",
        "roman_ur": "Yeh pehle hi handle ho chuka hai.",
        "ur": "یہ پہلے ہی نمٹایا جا چکا ہے۔",
    },
    "limit": {
        "en": "You have used all {limit} free AI messages for this month. The Add form still works as always.",
        "roman_ur": "Is mahine ke {limit} free AI messages khatam ho gaye. Add form pehle ki tarah kaam karta hai.",
        "ur": "اس مہینے کے {limit} مفت اے آئی پیغامات ختم ہو گئے ہیں۔ ایڈ فارم پہلے کی طرح کام کرتا ہے۔",
    },
    "breaker": {
        "en": "The AI assistant has reached its limit for today and is resting. Nothing was saved and your allowance was not used. The Add form works as always.",
        "roman_ur": "AI assistant aaj ki hadd tak pahunch gaya hai aur aaram kar raha hai. Kuch save nahi hua aur aap ka allowance istemal nahi hua. Add form kaam karta hai.",
        "ur": "اے آئی اسسٹنٹ آج کی حد تک پہنچ گیا ہے اور آرام کر رہا ہے۔ کچھ محفوظ نہیں ہوا اور آپ کا الاؤنس استعمال نہیں ہوا۔ ایڈ فارم کام کرتا ہے۔",
    },
    "unavailable": {
        "en": "I could not reach the assistant just now. Nothing was saved and your allowance was not used. Please try again, or use the Add form.",
        "roman_ur": "Abhi assistant tak nahi pahunch saka. Kuch save nahi hua aur aap ka allowance istemal nahi hua. Dobara koshish karein ya Add form istemal karein.",
        "ur": "ابھی اسسٹنٹ تک رسائی نہیں ہو سکی۔ کچھ محفوظ نہیں ہوا اور آپ کا الاؤنس استعمال نہیں ہوا۔ دوبارہ کوشش کریں یا ایڈ فارم استعمال کریں۔",
    },
    "amount": {
        "en": "I could not read the amount safely, so nothing was saved. Please write it in digits, like 1500, and send it again.",
        "roman_ur": "Raqam mehfooz tareeqe se parh nahi saka, is liye kuch save nahi hua. Barah-e-karam isay digits mein likhein, jaise 1500, aur dobara bhejein.",
        "ur": "رقم محفوظ طریقے سے پڑھی نہیں جا سکی، اس لیے کچھ محفوظ نہیں ہوا۔ براہ کرم اسے ہندسوں میں لکھیں، جیسے 1500، اور دوبارہ بھیجیں۔",
    },
    "date": {
        "en": "I could not read the date, so nothing was saved. Please say today or yesterday, or write it like 2026-11-19.",
        "roman_ur": "Tareekh parh nahi saka, is liye kuch save nahi hua. Aaj ya kal likhein, ya aise likhein 2026-11-19.",
        "ur": "تاریخ پڑھی نہیں جا سکی، اس لیے کچھ محفوظ نہیں ہوا۔ آج یا کل لکھیں، یا ایسے لکھیں 2026-11-19۔",
    },
    "category": {
        "en": "I could not tell the category, so nothing was saved. Please use the Add form for this one.",
        "roman_ur": "Category samajh nahi aayi, is liye kuch save nahi hua. Is ke liye Add form istemal karein.",
        "ur": "زمرہ سمجھ نہیں آیا، اس لیے کچھ محفوظ نہیں ہوا۔ اس کے لیے ایڈ فارم استعمال کریں۔",
    },
    "no_items": {
        "en": "I could not find an amount to save. Please write the amount in digits, like \"chai 50\".",
        "roman_ur": "Save karne ke liye koi raqam nahi mili. Raqam digits mein likhein, jaise \"chai 50\".",
        "ur": "محفوظ کرنے کے لیے کوئی رقم نہیں ملی۔ رقم ہندسوں میں لکھیں، جیسے \"چائے 50\"۔",
    },
    "too_many": {
        "en": "That is more than {n} items in one message, so nothing was saved. Please send fewer at a time.",
        "roman_ur": "Ek message mein {n} se zyada cheezein hain, is liye kuch save nahi hua. Ek waqt mein kam bhejein.",
        "ur": "ایک پیغام میں {n} سے زیادہ چیزیں ہیں، اس لیے کچھ محفوظ نہیں ہوا۔ ایک وقت میں کم بھیجیں۔",
    },
    "not_yet": {
        "en": "Asking about, or changing, existing entries by chat is coming soon. For now please use the Expenses page. Nothing was changed and your allowance was not used.",
        "roman_ur": "Chat se mojooda entries ke baare mein poochna ya badalna jald aa raha hai. Abhi Expenses page istemal karein. Kuch nahi badla aur aap ka allowance istemal nahi hua.",
        "ur": "چیٹ کے ذریعے موجودہ اندراجات کے بارے میں پوچھنا یا انہیں بدلنا جلد آ رہا ہے۔ ابھی اخراجات کا صفحہ استعمال کریں۔ کچھ نہیں بدلا اور آپ کا الاؤنس استعمال نہیں ہوا۔",
    },
    "save_failed": {
        "en": "I could not save that, so nothing was saved. Please use the Add form.",
        "roman_ur": "Yeh save nahi ho saka, is liye kuch save nahi hua. Add form istemal karein.",
        "ur": "یہ محفوظ نہیں ہو سکا، اس لیے کچھ محفوظ نہیں ہوا۔ ایڈ فارم استعمال کریں۔",
    },
}

_CARD_ERROR_KEY = {"expired": "expired", "already_used": "used", "cancelled": "used", "not_found": "expired",
                   "not_confirmed": "used", "undo_window_over": "used", "bad_choice": "used", "conflict": "used"}


def _say(kind: str, lang: str, **values) -> str:
    return T[kind].get(lang, T[kind]["en"]).format(**values)


def _pre_lang(text: str) -> str:
    """The language of a message before the model has read it (for replies that need no model)."""
    return {"ur": "ur", "roman": "roman_ur"}.get(detect_language(text), "en")


def _lang(value: str) -> str:
    return {"en": "en", "ur": "ur", "roman_ur": "roman_ur", "mixed": "roman_ur"}.get(value, "en")


def items_text(payload: pa.CardPayload, rows: list) -> str:
    """One line per item: amount, category (English names; the note is shown in the card, never in this text), date."""
    names = {slug: (en, ur) for slug, en, ur in rows}
    lines = []
    for item in payload.items:
        en, ur = names.get(item.category_slug, (item.category_slug,) * 2)
        lines.append(f"• {pa.format_rupees(item.amount_paisa)} · {ur if payload.lang == 'ur' else en} · {item.occurred_on.isoformat()}")
    return "\n".join(lines)


@dataclass
class Handled:
    answer: str
    card: Optional[dict] = None
    kind: str = "info"          # card | saved | cancelled | info: for tests and the app


# ── validation: from what the model copied to values the service layer accepts ─────────────────────

def build_payload(db, user_id: int, understood, text: str, lang: str, now, rows: list):
    """(CardPayload, None) or (None, problem). ONE bad item means no card at all."""
    if not understood.items:
        return None, "no_items"
    if len(understood.items) > pa.MAX_ITEMS:
        return None, "too_many"
    today = es._local_today(db, user_id, now)
    valid = {r[0] for r in rows}
    items = []
    for item in understood.items:
        if not amount_in_message(item.amount_text, text):          # the model must have copied it, not invented or changed it
            return None, "amount"
        try:
            paisa = normalise_amount(item.amount_text)
        except MoneyError:
            return None, "amount"
        try:
            day = parse_date_text(item.date_text, today, max_future_days=es.MAX_FUTURE_DAYS)
        except DateError:
            return None, "date"
        if item.category_slug is None or item.category_slug not in valid:
            return None, "category"
        note = es.clean_note(item.note)
        items.append(pa.CardItem(amount_paisa=paisa, category_slug=item.category_slug, occurred_on=day,
                                 note=note[:pa.MAX_NOTE_CHARS] if note else None, payment_method=item.payment_method or "cash",
                                 alternatives=pa.alternatives_for(item.category_slug, rows)))
    return pa.CardPayload(lang=lang, items=items), None


# ── the entry point ───────────────────────────────────────────────────────────────────────────────

def _answer_yes_no(db, user_id: int, row, answer: str, rows: list) -> Handled:
    lang = pa.CardPayload.model_validate(row.payload).lang
    try:
        if answer == "no":
            pa.cancel(db, user_id, row.id)
            return Handled(_say("cancelled", lang), kind="cancelled")
        saved = pa.confirm(db, user_id, row.id)
    except pa.CardError as err:
        return Handled(_say(_CARD_ERROR_KEY.get(err.code, "used"), lang), kind="info")
    except es.ExpenseError:
        return Handled(_say("save_failed", lang), kind="info")
    payload = pa.CardPayload.model_validate(row.payload)
    return Handled(_say("saved", lang, items=items_text(payload, rows)), kind="saved")


async def handle(db, user, message: str, client_msg_id: Optional[str] = None) -> Optional[Handled]:
    """See the module text. `user` is the authenticated user object: its id is the only identity used."""
    if not ai_limits.ai_enabled():
        return None
    text = clean_message(message)
    if not text:
        return None
    user_id = user.id
    now = es._utcnow()

    answer = yes_or_no(text)
    if answer is not None:
        waiting = pa.latest_pending(db, user_id, now)
        return _answer_yes_no(db, user_id, waiting, answer, taxonomy(db)) if waiting is not None else None

    if not looks_like_expense_talk(text):
        return None

    msg_id = client_msg_id if isinstance(client_msg_id, str) and _MSG_ID.match(client_msg_id) else uuid.uuid4().hex
    rows = taxonomy(db)
    earlier = pa.get_by_message(db, user_id, msg_id)
    if earlier is not None:                                     # the same message again: show the same card, spend nothing
        lang = pa.CardPayload.model_validate(earlier.payload).lang
        shown = pa.render(earlier, rows)
        body = _say("card", lang, items=items_text(pa.CardPayload.model_validate(earlier.payload), rows)) if shown["status"] == "pending" \
            else _say("expired" if shown["status"] == "expired" else "used", lang)
        return Handled(body, shown, "card")

    if ai_limits.breaker_open(db, now):
        return Handled(_say("breaker", _pre_lang(text)), kind="info")
    period = ai_limits.period_for(db, user_id, now)
    if ai_limits.try_consume(db, user_id, period) is None:
        return Handled(_say("limit", _pre_lang(text), limit=ai_limits.free_limit()), kind="info")

    try:
        result = await understand(db, text)
    except Exception as exc:                                    # a bug must never cost the person their allowance
        log.error("expense AI: understand failed (%s)", type(exc).__name__)
        ai_limits.release(db, user_id, period)
        return Handled(_say("unavailable", _pre_lang(text)), kind="info")
    if result.tokens:
        ai_limits.add_tokens(db, result.tokens, now)
    understood = result.understood
    if understood is None:
        ai_limits.release(db, user_id, period)
        return Handled(_say("unavailable", _pre_lang(text)), kind="info")

    lang = _lang(understood.language)
    if understood.intent in ("islamic_finance_qa", "off_topic", "unclear"):
        ai_limits.release(db, user_id, period)
        return None                                              # the normal chatbot (and its guard) answers
    if understood.intent in ("expense_query", "expense_edit_delete"):
        ai_limits.release(db, user_id, period)                   # Path step 5
        return Handled(_say("not_yet", lang), kind="info")

    payload, problem = build_payload(db, user_id, understood, text, lang, now, rows)
    if payload is None:
        ai_limits.release(db, user_id, period)
        return Handled(_say(problem, lang, n=pa.MAX_ITEMS), kind="info")
    row, created = pa.create_card(db, user_id, client_msg_id=msg_id, payload=payload, now=now)
    if not created:                                              # a parallel copy of this message won: it paid, we do not
        ai_limits.release(db, user_id, period)
    return Handled(_say("card", lang, items=items_text(payload, rows)), pa.render(row, rows), "card")


def usage_status(db, user_id: int) -> dict:
    """For the app (Path step 5): is the assistant on, how much of this month is used, is the daily breaker open."""
    now = es._utcnow()
    period = ai_limits.period_for(db, user_id, now)
    return {"enabled": ai_limits.ai_enabled(), "period": period, "limit": ai_limits.free_limit(),
            "used": ai_limits.used_this_period(db, user_id, period), "breaker_open": ai_limits.breaker_open(db, now)}
