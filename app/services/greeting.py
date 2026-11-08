"""
ShariahEase — greeting matching for the chat guards.

The guards let a greeting through ("hi", "salam", ...). They used to test
`greeting in text`, which is a substring test: "hi" matched inside "machine",
"this" and "which", and "hey" inside "they", so those off-topic questions skipped
the guard. A greeting now has to be a whole word.

  words : must be a whole word. A Latin word may be stretched by repeating its
          last letter ("hii", "heyy", "helloo").
  stems : must START a word and may continue ("assalam" matches "assalamu" and
          "assalamualaikum").
"""

import re
from functools import lru_cache
from typing import Iterable


def _one(term: str, *, stem: bool) -> str:
    term = term.strip().lower()
    if not term:
        return ""
    if term[-1].isascii() and term[-1].isalpha():
        body = re.escape(term[:-1]) + re.escape(term[-1]) + "+"   # "hi" -> hi+
    else:
        body = re.escape(term)
    return body if stem else body + r"(?!\w)"


@lru_cache(maxsize=32)
def _compile(words: tuple, stems: tuple):
    parts = [p for p in [_one(w, stem=False) for w in words] +
                        [_one(s, stem=True) for s in stems] if p]
    if not parts:
        return None
    return re.compile(r"(?<!\w)(?:" + "|".join(parts) + ")")


def contains_greeting(text: str, words: Iterable[str], stems: Iterable[str] = ()) -> bool:
    """True if `text` contains one of the greeting words (or a word starting with a stem)."""
    pattern = _compile(tuple(sorted(set(words))), tuple(sorted(set(stems))))
    return bool(pattern and pattern.search(text.lower()))
