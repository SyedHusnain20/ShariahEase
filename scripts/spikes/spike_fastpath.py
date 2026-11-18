"""
Step 4a, spike S6: how many messages could skip the AI completely?

A deliberately CAUTIOUS reader of the simplest message shape: ONE amount and ONE known word.
    "Petrol 3000"   "chai 50"   "bijli ka bill 8500 diya"   "uber 640"
Anything it does not fully understand (an unknown word, two amounts, a question, a date other than today,
two different categories) is NOT handled and would go to the model. No network, no model.
"""
import re
import unicodedata

KEYWORDS = {
    "food.tea_snacks": ["chai", "tea", "samosa", "چائے", "ناشتہ", "nashta"],
    "food.groceries": ["groceries", "grocery", "ration", "rashan", "راشن"],
    "food.dining_out": ["lunch", "dinner", "restaurant", "biryani"],
    "transport.fuel": ["petrol", "diesel", "fuel", "پٹرول"],
    "transport.ride_hailing": ["uber", "careem", "indrive", "bykea"],
    "transport.public": ["rickshaw", "bus", "metro"],
    "bills.electricity": ["electricity", "bijli", "بجلی"],
    "bills.gas": ["gas", "sui", "گیس"],
    "bills.internet": ["internet", "wifi", "انٹرنیٹ"],
    "bills.mobile": ["load", "jazz", "zong", "ufone", "موبائل"],
    "bills.rent": ["rent", "kiraya", "کرایہ"],
    "charity.zakat": ["zakat", "zakaat", "zakah", "زکوٰۃ", "زکوۃ"],
    "charity.sadaqah": ["sadaqah", "sadqa", "sadaqa", "صدقہ"],
    "health.medicine": ["medicine", "dawai", "dawa", "دوائی", "دوائیں"],
    "health.doctor": ["doctor", "ڈاکٹر"],
    "education.fees": ["fees", "fee", "فیس"],
    "subscriptions.streaming": ["netflix", "spotify", "youtube"],
}

# words that carry no category and no extra meaning
FILLER = {
    "spent", "paid", "pay", "gave", "bought", "buy", "rs", "rs.", "pkr", "rupees", "rupee", "rupay", "rupaye", "for",
    "on", "of", "the", "a", "my", "bill", "bills", "ka", "ki", "ke", "ko", "pe", "par", "diya", "diye", "kiya",
    "kiye", "di", "li", "liya", "kharch", "karwaya", "karwai", "aaj", "today", "کا", "کی", "کے", "پر", "دیا",
    "دی", "روپے", "آج", "ادا", "کیا", "لیا", "subscription", "load", "بل", "ڈلوایا", "ڈلوائی",
}
_AMOUNT = re.compile(r"^(?:rs\.?|pkr)?\d[\d,]*(?:\.\d{1,2})?$", re.IGNORECASE)
_DIGITS = {ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")}
_DIGITS.update({ord(c): str(i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")})
_QUESTION = ("?", "؟", "kitna", "how much", "kitne", "کتنا")
_WORDS = re.compile(r"[\w؀-ۿ.,]+", re.UNICODE)


def _normalise(text: str) -> str:
    return unicodedata.normalize("NFKC", text).translate(_DIGITS).strip().lower()


def read(message: str):
    """None when the message must go to the model; otherwise {'amount_text', 'category_slug'}."""
    text = _normalise(message)
    if not text or any(q in text for q in _QUESTION):
        return None
    words = [w.strip(".,") if not _AMOUNT.match(w) else w for w in _WORDS.findall(text)]
    amounts = [w for w in words if _AMOUNT.match(w)]
    if len(amounts) != 1:
        return None
    found = set()
    for w in words:
        if w in amounts:
            continue
        slugs = [s for s, kws in KEYWORDS.items() if w in kws]
        if slugs:
            found.update(slugs)
        elif w not in FILLER:
            return None                     # a word we do not know: let the model read it
    if len(found) != 1:
        return None                         # none, or two different categories
    amount = amounts[0]
    amount = re.sub(r"^(?:rs\.?|pkr)", "", amount, flags=re.IGNORECASE)
    return {"amount_text": amount, "category_slug": next(iter(found))}


def evaluate(gold, extra_messages=()):
    """Share handled, and how often a handled message was read correctly (gold messages only)."""
    handled, correct, wrong = [], 0, []
    for g in gold:
        got = read(g["text"])
        if got is None:
            continue
        handled.append(g["id"])
        want = g["items"][0] if g["intent"] == "expense_log" and len(g["items"]) == 1 else None
        want_amount = re.sub(r"^(?:rs\.?|pkr)\s*", "", _normalise(want[0]), flags=re.IGNORECASE) if want else None
        if want and got["category_slug"] == want[2] and got["amount_text"] == want_amount:
            correct += 1
        else:
            wrong.append({"id": g["id"], "got": got})
    extra = list(extra_messages)
    mine = [m for m in extra if read(m) is not None]
    return {
        "gold_total": len(gold), "gold_handled": len(handled), "gold_correct": correct, "wrong": wrong,
        "handled_ids": handled, "my_total": len(extra), "my_handled": len(mine), "my_handled_samples": mine[:10],
    }
