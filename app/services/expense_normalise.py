"""
Deterministic normalisers for the AI expense path (Path step 4b): amounts, dates and periods.

The model only COPIES what the person wrote ("2 hazar", "kal", "is mahine"). This file turns those words into numbers
and dates. Rules:

  * Money has ONE strict gate, app/services/money.py. Plain numbers go straight to it; "5k", "1.5 lakh" and "do hazar"
    are split into a number and a multiplier here, the number goes through the same gate, and the multiplication is done
    in whole integers. No floats anywhere.
  * Anything not understood is REFUSED (a stable error code), never guessed. The person is asked to type it differently.
  * `amount_in_message` is the cross-check: the model's copy of the amount must really be in the message, as a whole
    number or phrase (so "500" cannot be taken from "5000"). A model that invents or changes an amount is caught here.
  * Dates and periods are worked out from the person's own local today, which the caller passes in.
  * Words are matched after Unicode normalisation (NFKC), lower-casing and mapping Urdu/Arabic digits to 0-9.

Urdu words in the tables below are marked for native-speaker review, like the rest of the Urdu text.
"""
import re
import unicodedata
from datetime import date, timedelta
from typing import Optional

from app.database.models import MAX_AMOUNT_PAISA
from app.services.money import MoneyError, parse_rupees_to_paisa

# ── folding ───────────────────────────────────────────────────────────────────────────────────────

_DIGITS = {ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")}
_DIGITS.update({ord(c): str(i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")})
_DIGITS.update({ord("٫"): ".", ord("٬"): ","})
_SPACES = re.compile(r"\s+")


def fold(text) -> str:
    """NFKC, lower case, Urdu digits to 0-9, one space between words. Not text a person ever sees again."""
    if not isinstance(text, str):
        return ""
    return _SPACES.sub(" ", unicodedata.normalize("NFKC", text).translate(_DIGITS).casefold()).strip()


# ── amounts ───────────────────────────────────────────────────────────────────────────────────────

# value in hundredths of a rupee (so 2.5 is 250); small numbers only, anything bigger is typed in digits
_WORD_VALUES = {
    # English
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
    # Roman Urdu
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "panch": 5, "paanch": 5, "chhe": 6, "chay": 6, "che": 6,
    "saat": 7, "aath": 8, "nau": 9, "das": 10, "gyarah": 11, "barah": 12, "terah": 13, "chaudah": 14, "pandrah": 15,
    "solah": 16, "satrah": 17, "atharah": 18, "unnees": 19, "bees": 20, "tees": 30, "chalis": 40, "pachas": 50,
    "saath": 60, "sattar": 70, "assi": 80, "nabbe": 90,
    # Urdu script (needs review)
    "ایک": 1, "دو": 2, "تین": 3, "چار": 4, "پانچ": 5, "چھ": 6, "سات": 7, "آٹھ": 8, "نو": 9, "دس": 10, "بیس": 20,
    "تیس": 30, "چالیس": 40, "پچاس": 50, "ساٹھ": 60, "ستر": 70, "اسی": 80, "نوے": 90,
}
_WORD_VALUES = {word: value * 100 for word, value in _WORD_VALUES.items()}
_WORD_VALUES.update({"derh": 150, "dedh": 150, "ڈیڑھ": 150, "dhai": 250, "dhaai": 250, "ڈھائی": 250})

_MULTIPLIERS = {
    "sau": 100, "سو": 100, "hundred": 100,
    "k": 1_000, "hazar": 1_000, "hazaar": 1_000, "thousand": 1_000, "ہزار": 1_000,
    "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000, "لاکھ": 100_000,
    "crore": 10_000_000, "crores": 10_000_000, "karor": 10_000_000, "کروڑ": 10_000_000,
}
_IGNORED = frozenset({"rs", "rs.", "pkr", "₨", "rupay", "rupaye", "rupees", "rupee", "روپے", "روپیہ", "روپیے", "rupya"})
_NUMBER = re.compile(r"^[\d][\d.,]*$")
_GLUED = re.compile(r"^(?P<n>\d[\d.,]*)(?P<m>[^\d.,\s]+)$")
_RS_GLUED = re.compile(r"^(?:rs\.?|pkr|₨)(?=\d)")


def _number_value(token: str) -> int:
    """A plain number or one number word, in hundredths of a rupee. Digits go through the strict money gate."""
    if _NUMBER.match(token):
        return parse_rupees_to_paisa(token)
    if token in _WORD_VALUES:
        return _WORD_VALUES[token]
    raise MoneyError("format")


def normalise_amount(text) -> int:
    """
    The amount as integer paisa, or MoneyError(code). Understands:  1,250.50   Rs 500   ۸۵۰۰   5k   1.5 lakh
    2 hazar   do hazar   دو ہزار   dhai sau   ڈیڑھ لاکھ.   Refuses everything else (two amounts, "about 500", 1e3...).
    """
    if not isinstance(text, str):
        raise MoneyError("format")
    cleaned = fold(text)
    cleaned = _RS_GLUED.sub("", cleaned)
    tokens = [t for t in cleaned.split(" ") if t and t not in _IGNORED]
    if not tokens:
        raise MoneyError("empty")
    if len(tokens) == 1:
        glued = _GLUED.match(tokens[0])
        if glued and glued.group("m") in _MULTIPLIERS:          # 5k, 2lakh
            tokens = [glued.group("n"), glued.group("m")]
    if len(tokens) == 1:
        return _number_value(tokens[0])                          # digits, or one number word; anything else is a format error
    if len(tokens) == 2 and tokens[1] in _MULTIPLIERS:
        hundredths = _number_value(tokens[0])
        paisa = hundredths * _MULTIPLIERS[tokens[1]]
        if paisa > MAX_AMOUNT_PAISA:
            raise MoneyError("too_large")
        return paisa
    raise MoneyError("format")


def amount_in_message(amount_text, message) -> bool:
    """The model's copy of the amount must be in the message, as a whole number or phrase (digits folded to 0-9)."""
    needle, haystack = fold(amount_text), fold(message)
    if not needle or not haystack:
        return False
    pattern = r"(?<!\w)(?<!\d[.,])" + re.escape(needle) + r"(?!\w)(?![.,]\d)"
    return re.search(pattern, haystack) is not None


# ── dates ─────────────────────────────────────────────────────────────────────────────────────────

class DateError(ValueError):
    """`code` is stable: unknown, invalid, future."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


_TODAY = frozenset({"today", "aaj", "aj", "آج", "abhi", "now"})
_YESTERDAY = frozenset({"yesterday", "kal", "kl", "کل", "guzishta kal", "گزشتہ کل"})     # "kal" in a spending message = yesterday
_DAY_BEFORE = frozenset({"parson", "parso", "parsun", "پرسوں", "day before yesterday"})
_DAYS_AGO = re.compile(r"^(\d{1,2}) (?:din|days?|دن) (?:pehle|pahle|ago|پہلے)$")
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_DAY_FIRST = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{4}|\d{2}))?$")
DATE_FLOOR = date(2000, 1, 1)
MAX_DAYS_AGO = 31


def parse_date_text(text, today: date, *, max_future_days: int = 1) -> date:
    """The date the person wrote, as a local date. Empty means today. Raises DateError; never guesses."""
    if text is None or (isinstance(text, str) and not text.strip()):
        return today
    word = fold(text).strip(" .,!؟?")
    if not word:
        return today
    if word in _TODAY:
        return today
    if word in _YESTERDAY:
        return today - timedelta(days=1)
    if word in _DAY_BEFORE:
        return today - timedelta(days=2)
    ago = _DAYS_AGO.match(word)
    if ago:
        n = int(ago.group(1))
        if n > MAX_DAYS_AGO:
            raise DateError("unknown")
        return today - timedelta(days=n)
    found = _ISO.match(word)
    if found:
        year, month, day = (int(g) for g in found.groups())
    else:
        found = _DAY_FIRST.match(word)                          # Pakistan writes day first: 05/11/2026
        if not found:
            raise DateError("unknown")
        day, month = int(found.group(1)), int(found.group(2))
        raw_year = found.group(3)
        year = today.year if raw_year is None else (2000 + int(raw_year) if len(raw_year) == 2 else int(raw_year))
    try:
        value = date(year, month, day)
    except ValueError:
        raise DateError("invalid") from None
    if value < DATE_FLOOR:
        raise DateError("invalid")
    if value > today + timedelta(days=max_future_days):
        raise DateError("future")
    return value


# ── periods ───────────────────────────────────────────────────────────────────────────────────────

_PERIODS = {
    "today": ("day", 0), "aaj": ("day", 0), "آج": ("day", 0),
    "yesterday": ("day", -1), "kal": ("day", -1), "کل": ("day", -1),
    "this week": ("week", 0), "is hafte": ("week", 0), "is haftay": ("week", 0), "اس ہفتے": ("week", 0),
    "last week": ("week", -1), "pichle hafte": ("week", -1), "پچھلے ہفتے": ("week", -1),
    "this month": ("month", 0), "is mahine": ("month", 0), "is month": ("month", 0), "اس مہینے": ("month", 0), "اس ماہ": ("month", 0),
    "last month": ("month", -1), "pichle mahine": ("month", -1), "guzishta mahine": ("month", -1),
    "پچھلے مہینے": ("month", -1), "گزشتہ ماہ": ("month", -1),
    "this year": ("year", 0), "is saal": ("year", 0), "اس سال": ("year", 0),
    "last year": ("year", -1), "pichle saal": ("year", -1), "پچھلے سال": ("year", -1),
}


def _shift_month(year: int, month: int, by: int):
    index = year * 12 + (month - 1) + by
    return index // 12, index % 12 + 1


def parse_period_text(text, today: date) -> Optional[tuple]:
    """(date_from, date_to) as a [from, to) window in the person's local dates; empty means this month; unknown is None."""
    word = fold(text).strip(" .,!؟?") if isinstance(text, str) else ""
    unit, offset = _PERIODS.get(word, ("month", 0) if not word else (None, 0))
    if unit is None:
        return None
    if unit == "day":
        start = today + timedelta(days=offset)
        return start, start + timedelta(days=1)
    if unit == "week":                                           # weeks run Monday to Sunday
        start = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)
        return start, start + timedelta(days=7)
    if unit == "month":
        y, m = _shift_month(today.year, today.month, offset)
        ny, nm = _shift_month(y, m, 1)
        return date(y, m, 1), date(ny, nm, 1)
    return date(today.year + offset, 1, 1), date(today.year + offset + 1, 1, 1)
