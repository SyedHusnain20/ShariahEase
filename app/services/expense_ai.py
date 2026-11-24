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
  7. the intent: other topics go back to the normal chatbot (allowance given back)
  8. an expense: every amount is cross-checked against the message and normalised by code, dates and categories are
     validated; ONE problem anywhere means NO card at all (all or nothing), allowance given back
  9. a card is stored; only the person's "yes" writes the expense
 10. (step 5a) a question ("how much did I spend...?") is answered from SQL totals with a fixed template: no model narration
 11. (step 5a) a change or delete of an existing entry makes a card too: one entry named by the person's own words, or a numbered
     list to pick from ("2"); only "yes" changes anything, and only if the entry is still as the card showed it

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
from app.services.expense_edit import build_edit
from app.services.expense_query import QueryResult, run_query, unexpected_numbers
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
    "edit_update": {
        "en": "I understood: change this entry:\n{before}\nto:\n{after}\nReply \"yes\" to save the change, or \"no\" to cancel. This stays open for 15 minutes.",
        "roman_ur": "Maine yeh samjha: is entry ko badalna hai:\n{before}\nyeh ban jaye gi:\n{after}\nBadalne ke liye \"haan\" likhein, cancel ke liye \"nahi\". Yeh 15 minute tak khula hai.",
        "ur": "میں نے یہ سمجھا: اس اندراج کو بدلنا ہے:\n{before}\nیہ بن جائے گا:\n{after}\nتبدیلی محفوظ کرنے کے لیے \"ہاں\" لکھیں، منسوخ کرنے کے لیے \"نہیں\"۔ یہ 15 منٹ تک کھلا ہے۔",
    },
    "edit_delete": {
        "en": "I understood: delete this entry:\n{before}\nReply \"yes\" to delete it, or \"no\" to cancel. This stays open for 15 minutes.",
        "roman_ur": "Maine yeh samjha: yeh entry delete karni hai:\n{before}\nDelete karne ke liye \"haan\" likhein, cancel ke liye \"nahi\". Yeh 15 minute tak khula hai.",
        "ur": "میں نے یہ سمجھا: یہ اندراج حذف کرنا ہے:\n{before}\nحذف کرنے کے لیے \"ہاں\" لکھیں، منسوخ کرنے کے لیے \"نہیں\"۔ یہ 15 منٹ تک کھلا ہے۔",
    },
    "pick": {
        "en": "Which one do you mean? Reply with its number (1 to {n}), or \"no\" to cancel:\n{items}\nThis stays open for 15 minutes.",
        "roman_ur": "Aap kaun si entry ki baat kar rahe hain? Uska number likhein (1 se {n}), ya cancel ke liye \"nahi\":\n{items}\nYeh 15 minute tak khula hai.",
        "ur": "آپ کون سا اندراج مراد لے رہے ہیں؟ اس کا نمبر لکھیں (1 سے {n}) یا منسوخ کرنے کے لیے \"نہیں\":\n{items}\nیہ 15 منٹ تک کھلا ہے۔",
    },
    "pick_more": {
        "en": "More entries fit. These are the most recent {n}.",
        "roman_ur": "Aur entries bhi match karti hain. Yeh sab se nayi {n} hain.",
        "ur": "مزید اندراجات بھی ملتے ہیں۔ یہ سب سے نئے {n} ہیں۔",
    },
    "pick_range": {
        "en": "Please reply with a number from 1 to {n}, or \"no\" to cancel.",
        "roman_ur": "Barah-e-karam 1 se {n} tak koi number likhein, ya cancel ke liye \"nahi\".",
        "ur": "براہ کرم 1 سے {n} تک کوئی نمبر لکھیں، یا منسوخ کرنے کے لیے \"نہیں\"۔",
    },
    "which_first": {
        "en": "Please pick which entry first: reply with its number from the list, or \"no\" to cancel.",
        "roman_ur": "Pehle entry chunein: list mein se uska number likhein, ya cancel ke liye \"nahi\".",
        "ur": "پہلے اندراج منتخب کریں: فہرست میں سے اس کا نمبر لکھیں، یا منسوخ کرنے کے لیے \"نہیں\"۔",
    },
    "updated": {
        "en": "Changed:\n{before}\nto:\n{after}",
        "roman_ur": "Badal diya:\n{before}\nab:\n{after}",
        "ur": "بدل دیا:\n{before}\nاب:\n{after}",
    },
    "deleted": {
        "en": "Deleted:\n{before}",
        "roman_ur": "Delete ho gaya:\n{before}",
        "ur": "حذف ہو گیا:\n{before}",
    },
    "stale": {
        "en": "That entry has changed since I showed it, so nothing was changed. Please ask again.",
        "roman_ur": "Yeh entry dikhane ke baad badal chuki hai, is liye kuch nahi badla. Dobara poochhein.",
        "ur": "یہ اندراج دکھانے کے بعد بدل چکا ہے، اس لیے کچھ نہیں بدلا۔ دوبارہ پوچھیں۔",
    },
    "no_match": {
        "en": "I could not find a matching entry, so nothing was changed. Try including its amount or date. Your allowance was not used.",
        "roman_ur": "Koi milti julti entry nahi mili, is liye kuch nahi badla. Raqam ya tareekh bhi likhein. Aap ka allowance istemal nahi hua.",
        "ur": "کوئی ملتا جلتا اندراج نہیں ملا، اس لیے کچھ نہیں بدلا۔ رقم یا تاریخ بھی لکھیں۔ آپ کا الاؤنس استعمال نہیں ہوا۔",
    },
    "unclear_target": {
        "en": "I could not tell which entry you mean, so nothing was changed. Please say its amount or date. Your allowance was not used.",
        "roman_ur": "Samajh nahi aaya aap kaun si entry ki baat kar rahe hain, is liye kuch nahi badla. Uski raqam ya tareekh likhein. Aap ka allowance istemal nahi hua.",
        "ur": "سمجھ نہیں آیا آپ کون سا اندراج مراد لے رہے ہیں، اس لیے کچھ نہیں بدلا۔ اس کی رقم یا تاریخ لکھیں۔ آپ کا الاؤنس استعمال نہیں ہوا۔",
    },
    "need_change": {
        "en": "Please say what to do: delete the entry, or change its amount or category. Nothing was changed and your allowance was not used.",
        "roman_ur": "Batayein kya karna hai: entry delete karni hai, ya uski raqam ya category badalni hai. Kuch nahi badla aur aap ka allowance istemal nahi hua.",
        "ur": "بتائیں کیا کرنا ہے: اندراج حذف کرنا ہے، یا اس کی رقم یا زمرہ بدلنا ہے۔ کچھ نہیں بدلا اور آپ کا الاؤنس استعمال نہیں ہوا۔",
    },
    "one_at_a_time": {
        "en": "I can change or delete one entry at a time, so nothing was changed. Your allowance was not used.",
        "roman_ur": "Main ek waqt mein sirf ek entry badal ya delete kar sakta hoon, is liye kuch nahi badla. Aap ka allowance istemal nahi hua.",
        "ur": "میں ایک وقت میں صرف ایک اندراج بدل یا حذف کر سکتا ہوں، اس لیے کچھ نہیں بدلا۔ آپ کا الاؤنس استعمال نہیں ہوا۔",
    },
    "period": {
        "en": "I could not read the period. Try \"this month\", \"last month\", \"today\", \"this week\" or \"this year\". Your allowance was not used.",
        "roman_ur": "Muddat samajh nahi aayi. \"is mahine\", \"pichle mahine\", \"aaj\", \"is hafte\" ya \"is saal\" likh kar dekhein. Aap ka allowance istemal nahi hua.",
        "ur": "مدت سمجھ نہیں آئی۔ \"اس مہینے\"، \"پچھلے مہینے\"، \"آج\"، \"اس ہفتے\" یا \"اس سال\" لکھ کر دیکھیں۔ آپ کا الاؤنس استعمال نہیں ہوا۔",
    },
    "q_category": {
        "en": "I could not tell which category you mean. Please use the Expenses page. Your allowance was not used.",
        "roman_ur": "Samajh nahi aaya aap kaun si category poochh rahe hain. Expenses page istemal karein. Aap ka allowance istemal nahi hua.",
        "ur": "سمجھ نہیں آیا آپ کون سا زمرہ پوچھ رہے ہیں۔ اخراجات کا صفحہ استعمال کریں۔ آپ کا الاؤنس استعمال نہیں ہوا۔",
    },
    "q_head": {
        "en": "Your spending from {start} to {end} (amount · number of entries):\n• Total: {total} · {n}{filter}{groups}",
        "roman_ur": "Aap ka kharcha {start} se {end} tak (raqam · entries ki tadaad):\n• Kul: {total} · {n}{filter}{groups}",
        "ur": "آپ کا خرچ {start} سے {end} تک (رقم · اندراجات کی تعداد):\n• کل: {total} · {n}{filter}{groups}",
    },
    "q_none": {
        "en": "No entries from {start} to {end}.{filter}",
        "roman_ur": "{start} se {end} tak koi entry nahi.{filter}",
        "ur": "{start} سے {end} تک کوئی اندراج نہیں۔{filter}",
    },
    "q_filter": {
        "en": "\n• Category: {name}",
        "roman_ur": "\n• Category: {name}",
        "ur": "\n• زمرہ: {name}",
    },
    "q_more": {
        "en": "\n…and {n} more.",
        "roman_ur": "\n…aur {n} aur.",
        "ur": "\n…اور {n} مزید۔",
    },
    "query_failed": {
        "en": "I could not answer that safely, so please use the Expenses page. Your allowance was not used.",
        "roman_ur": "Main is ka mehfooz jawab nahi de saka, is liye Expenses page istemal karein. Aap ka allowance istemal nahi hua.",
        "ur": "میں اس کا محفوظ جواب نہیں دے سکا، اس لیے اخراجات کا صفحہ استعمال کریں۔ آپ کا الاؤنس استعمال نہیں ہوا۔",
    },
    "save_failed": {
        "en": "I could not save that, so nothing was saved. Please use the Add form.",
        "roman_ur": "Yeh save nahi ho saka, is liye kuch save nahi hua. Add form istemal karein.",
        "ur": "یہ محفوظ نہیں ہو سکا، اس لیے کچھ محفوظ نہیں ہوا۔ ایڈ فارم استعمال کریں۔",
    },
}

_CARD_ERROR_KEY = {"expired": "expired", "already_used": "used", "cancelled": "used", "not_found": "expired",
                   "not_confirmed": "used", "undo_window_over": "used", "bad_choice": "used", "conflict": "used",
                   "stale": "stale", "not_chosen": "which_first"}


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


def _names(rows: list) -> dict:
    return {slug: (en, ur) for slug, en, ur in rows}


def edit_line(target: pa.EditTarget, rows: list, lang: str, *, amount_paisa=None, category_slug=None) -> str:
    """One entry in one line: amount, category, date. The note is never put in the text (only in the card JSON)."""
    slug = category_slug or target.category_slug
    en, ur = _names(rows).get(slug, (slug, slug))
    amount = target.amount_paisa if amount_paisa is None else amount_paisa
    return f"{pa.format_rupees(amount)} · {ur if lang == 'ur' else en} · {target.occurred_on.isoformat()}"


def edit_text(payload: pa.EditPayload, rows: list, truncated: bool = False) -> str:
    """What the person reads for an update/delete card: a numbered list to pick from, or the one entry and what happens to it."""
    lang = payload.lang
    if payload.chosen is None:
        lines = "\n".join(f"{i}. {edit_line(t, rows, lang)}" for i, t in enumerate(payload.candidates, start=1))
        text = _say("pick", lang, n=len(payload.candidates), items=lines)
        return _say("pick_more", lang, n=len(payload.candidates)) + "\n" + text if truncated else text
    target = payload.candidates[payload.chosen]
    before = edit_line(target, rows, lang)
    if payload.action == "delete":
        return _say("edit_delete", lang, before=before)
    return _say("edit_update", lang, before=before, after=_after_line(target, payload.changes, rows, lang))


def _after_line(target: pa.EditTarget, changes: pa.EditChanges, rows: list, lang: str) -> str:
    return edit_line(target, rows, lang, amount_paisa=changes.new_amount_paisa, category_slug=changes.new_category_slug)


def card_text(row, rows: list) -> str:
    """The reply text for a card that is waiting, whatever its kind."""
    payload = pa.payload_of(row)
    if row.kind == "create":
        return _say("card", payload.lang, items=items_text(payload, rows))
    return edit_text(payload, rows)


def _money(paisa: int) -> str:
    return pa.format_rupees(paisa)


def query_text(result: QueryResult, lang: str) -> str:
    """The answer to a question: a fixed template filled with the numbers SQL returned. Nothing is added up here."""
    filter_text = ""
    if result.category_slug is not None:
        name = result.category_ur if lang == "ur" else result.category_en
        filter_text = _say("q_filter", lang, name=name)
    start, end = result.date_from.isoformat(), result.last_day.isoformat()
    if result.count == 0:
        return _say("q_none", lang, start=start, end=end, filter=filter_text)
    groups = ""
    if result.groups:
        lines = []
        for g in result.groups:
            label = g.label_ur if lang == "ur" else g.label_en
            lines.append(f"• {label}: {_money(g.total_paisa)} · {g.count}")
        groups = "\n" + "\n".join(lines)
        if result.hidden:
            groups += _say("q_more", lang, n=result.hidden)
    return _say("q_head", lang, start=start, end=end, total=_money(result.total_paisa), n=result.count,
                filter=filter_text, groups=groups)


def query_data_text(result: QueryResult) -> str:
    """Every number the query returned, written out: the only numbers its answer may contain."""
    parts = [result.date_from.isoformat(), result.last_day.isoformat(), _money(result.total_paisa), str(result.count), str(result.hidden)]
    for g in result.groups:
        parts += [_money(g.total_paisa), str(g.count), g.key]
    return " ".join(parts)


def pick_number(text: str) -> Optional[int]:
    """A bare digit typed while a "which one?" list is waiting (0 and numbers beyond the list are answered with the range)."""
    word = fold(text).strip(" .!،۔؟?")
    return int(word) if re.fullmatch(r"[0-9]", word) else None


@dataclass
class Handled:
    answer: str
    card: Optional[dict] = None
    kind: str = "info"          # card | saved | cancelled | answer | info: for tests and the app


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
    payload = pa.payload_of(row)
    lang = payload.lang
    try:
        if answer == "no":
            pa.cancel(db, user_id, row.id)
            return Handled(_say("cancelled", lang), kind="cancelled")
        pa.confirm(db, user_id, row.id)
    except pa.CardError as err:
        card = pa.render(row, rows) if err.code == "not_chosen" else None
        return Handled(_say(_CARD_ERROR_KEY.get(err.code, "used"), lang), card, "card" if card else "info")
    except es.ExpenseError:
        return Handled(_say("save_failed", lang), kind="info")
    if row.kind == "create":
        return Handled(_say("saved", lang, items=items_text(payload, rows)), kind="saved")
    target = payload.candidates[payload.chosen]
    before = edit_line(target, rows, lang)
    if row.kind == "delete":
        return Handled(_say("deleted", lang, before=before), kind="saved")
    return Handled(_say("updated", lang, before=before, after=_after_line(target, payload.changes, rows, lang)), kind="saved")


def _answer_pick(db, user_id: int, row, number: int, rows: list) -> Handled:
    payload = pa.payload_of(row)
    if not 1 <= number <= len(payload.candidates):
        return Handled(_say("pick_range", payload.lang, n=len(payload.candidates)), pa.render(row, rows), "card")
    try:
        updated = pa.pick(db, user_id, row.id, number - 1)
    except pa.CardError as err:
        return Handled(_say(_CARD_ERROR_KEY.get(err.code, "used"), payload.lang), kind="info")
    return Handled(edit_text(pa.payload_of(updated), rows), pa.render(updated, rows), "card")


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

    number = pick_number(text)
    if number is not None:                                      # "2" while a "which one?" list is waiting
        waiting = pa.latest_pending(db, user_id, now)
        if waiting is not None and waiting.kind != "create":
            return _answer_pick(db, user_id, waiting, number, taxonomy(db))
        return None                                             # a lone digit with no list waiting is not an expense: no model call

    if not looks_like_expense_talk(text):
        return None

    msg_id = client_msg_id if isinstance(client_msg_id, str) and _MSG_ID.match(client_msg_id) else uuid.uuid4().hex
    rows = taxonomy(db)
    earlier = pa.get_by_message(db, user_id, msg_id)
    if earlier is not None:                                     # the same message again: show the same card, spend nothing
        lang = pa.payload_of(earlier).lang
        shown = pa.render(earlier, rows)
        body = card_text(earlier, rows) if shown["status"] == "pending" \
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
    try:
        if understood.intent == "expense_query":
            return _serve_query(db, user_id, understood, text, lang, now, rows, period)
        if understood.intent == "expense_edit_delete":
            return _serve_edit(db, user_id, understood, text, lang, now, rows, period, msg_id)
        return _serve_expense(db, user_id, understood, text, lang, now, rows, period, msg_id)
    except Exception as exc:                                     # a bug must never cost the person their allowance
        log.error("expense AI: serving failed (%s)", type(exc).__name__)
        db.rollback()
        ai_limits.release(db, user_id, period)
        return Handled(_say("unavailable", lang), kind="info")


def _serve_expense(db, user_id: int, understood, text: str, lang: str, now, rows: list, period: str, msg_id: str) -> Handled:
    payload, problem = build_payload(db, user_id, understood, text, lang, now, rows)
    if payload is None:
        ai_limits.release(db, user_id, period)
        return Handled(_say(problem, lang, n=pa.MAX_ITEMS), kind="info")
    row, created = pa.create_card(db, user_id, client_msg_id=msg_id, payload=payload, now=now)
    if not created:                                              # a parallel copy of this message won: it paid, we do not
        ai_limits.release(db, user_id, period)
    return Handled(_say("card", lang, items=items_text(payload, rows)), pa.render(row, rows), "card")


def _serve_query(db, user_id: int, understood, text: str, lang: str, now, rows: list, period: str) -> Handled:
    result, problem = run_query(db, user_id, understood.query, text, es._local_today(db, user_id, now), rows)
    if result is None:
        ai_limits.release(db, user_id, period)
        return Handled(_say("q_category" if problem == "category" else problem, lang), kind="info")
    answer = query_text(result, lang)
    if unexpected_numbers(answer, query_data_text(result)):      # the tripwire: no number the data did not give
        log.error("expense AI: a query answer held a number that was not in the data")
        ai_limits.release(db, user_id, period)
        return Handled(_say("query_failed", lang), kind="info")
    return Handled(answer, kind="answer")


def _serve_edit(db, user_id: int, understood, text: str, lang: str, now, rows: list, period: str, msg_id: str) -> Handled:
    outcome = build_edit(db, user_id, understood, text, lang, es._local_today(db, user_id, now), rows)
    if outcome.payload is None:
        ai_limits.release(db, user_id, period)
        return Handled(_say(outcome.problem, lang), kind="info")
    row, created = pa.create_card(db, user_id, client_msg_id=msg_id, payload=outcome.payload, now=now)
    if not created:
        ai_limits.release(db, user_id, period)
    return Handled(edit_text(pa.payload_of(row), rows, outcome.truncated and created), pa.render(row, rows), "card")


def usage_status(db, user_id: int) -> dict:
    """For the app (Path step 5): is the assistant on, how much of this month is used, is the daily breaker open."""
    now = es._utcnow()
    period = ai_limits.period_for(db, user_id, now)
    return {"enabled": ai_limits.ai_enabled(), "period": period, "limit": ai_limits.free_limit(),
            "used": ai_limits.used_this_period(db, user_id, period), "breaker_open": ai_limits.breaker_open(db, now)}
