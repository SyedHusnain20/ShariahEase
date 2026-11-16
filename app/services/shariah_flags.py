"""
Conservative, rule-based Shariah reminders for expenses. A flag is a reminder to ask a scholar, never a verdict.

Two sources decide an expense's flag:
  1. the subcategory's default flag (set in migration 0004: markup/interest and late charges are "riba",
     conventional loan instalments and insurance are "doubtful");
  2. a small keyword rule on the note the person typed (this file).
The stronger of the two wins ("riba" is stronger than "doubtful").

Why the keyword rule is deliberately small
  * A wrong flag costs trust, so only words that almost always mean interest are "riba".
  * "markup" is also an ordinary trade word (cost-plus selling is permitted), so on its own it is only "doubtful".
  * Whole words only: "interesting" and "interested" never match. "سودا" (goods, shopping) never matches "سود".
  * Phrases that say there is NO interest ("interest free", "bila sood", "بلا سود") are removed first.

To extend the rule, add words to the three tuples below. Use lowercase Latin (no accents) or Urdu/Arabic letters
without vowel marks; the text is normalised the same way before it is compared. Keep the list short.
Urdu spellings here are awaiting native-speaker review (see the hand-off, section 6).
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

# ── the lists you may extend ────────────────────────────────────────────────────────────────────

# Words that almost always mean interest / riba  ->  flag "riba"
STRONG_TERMS = (
    "interest", "interests", "riba", "ribaa", "usury",
    "sood", "sud", "soodi",                      # Roman Urdu
    "سود", "سودی", "ربا", "ربوا",                  # Urdu / Arabic script
)

# Words that are only sometimes about interest  ->  flag "doubtful"
WEAK_TERMS = (
    "markup", "mark up",
    "مارک اپ", "مارکاپ",
)

# Phrases that say there is no interest. They are removed before the words above are looked for.
NEGATIONS = (
    "interest free", "interestfree", "free of interest", "no interest", "without interest", "zero interest",
    "non interest", "interestless", "riba free", "free of riba", "no riba", "without riba",
    "bila sood", "bila sud", "bilasood", "sood free", "sood se paak", "sood se pak", "sood ke baghair",
    "sood k baghair", "baghair sood", "bagair sood", "beghair sood", "bina sood", "sood nahi", "sood nahin",
    "بلا سود", "بلاسود", "بغیر سود", "سود کے بغیر", "سود سے پاک", "سود فری", "سود نہیں", "بلا ربا", "ربا سے پاک",
)

# ── the rule ────────────────────────────────────────────────────────────────────────────────────

Flag = Optional[str]                     # "riba", "doubtful" or None
_RANK = {"riba": 2, "doubtful": 1}
_VALID = frozenset(_RANK)

# characters that are part of a word: Latin letters and digits, Arabic-script letters and digits (but not the Arabic
# comma, semicolon, question mark or full stop), and the zero-width joiners Urdu uses inside words
_LETTER = "0-9a-zؠ-ي٠-٩ٮ-ۓە۰-ۿݐ-ݿ‌‍"
_SEPARATORS = re.compile(r"[\-_‐‑–—]+")
_TATWEEL = "ـ"
_LETTER_MAP = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک"})   # Arabic yeh/kaf -> Urdu yeh/kaf


def normalise(text: str) -> str:
    """Lowercase, no accents or vowel marks, one kind of yeh/kaf, hyphens and underscores as spaces, single spaces."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn" and ch != _TATWEEL)
    text = text.casefold().translate(_LETTER_MAP)
    text = _SEPARATORS.sub(" ", text)
    return " ".join(text.split())


def _compile(terms) -> re.Pattern:
    words = sorted({normalise(t) for t in terms}, key=len, reverse=True)
    body = "|".join(r"\s+".join(re.escape(part) for part in w.split(" ")) for w in words)
    return re.compile(rf"(?<![{_LETTER}])(?:{body})(?![{_LETTER}])")


_STRONG = _compile(STRONG_TERMS)
_WEAK = _compile(WEAK_TERMS)
_NEGATION = _compile(NEGATIONS)


def keyword_flag(note: Optional[str]) -> Flag:
    """The flag a note's wording alone would raise: "riba", "doubtful" or None."""
    if not note or not isinstance(note, str):
        return None
    text = _NEGATION.sub(" ", normalise(note))
    if _STRONG.search(text):
        return "riba"
    if _WEAK.search(text):
        return "doubtful"
    return None


def stronger(a: Flag, b: Flag) -> Flag:
    """The stronger of two flags ("riba" beats "doubtful" beats None). Unknown values count as no flag."""
    a = a if a in _VALID else None
    b = b if b in _VALID else None
    if a is None:
        return b
    if b is None:
        return a
    return a if _RANK[a] >= _RANK[b] else b


def flag_for(default_flag: Flag, note: Optional[str]) -> Flag:
    """The flag an expense gets: its subcategory's default, raised by the note's wording if that is stronger."""
    return stronger(default_flag, keyword_flag(note))
