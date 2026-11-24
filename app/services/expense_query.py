"""
"How much did I spend?" for the AI expense assistant (Path step 5a).

The model only fills in a form (a period in the person's words, maybe a category, maybe a grouping). Everything else is code:

  * the period words are read by expense_normalise.parse_period_text and must literally be in the message,
  * the numbers come from expense_service.summarize, which does all the arithmetic in SQL on integer paisa,
  * this module adds nothing up. It selects, orders and cuts the list; it never calculates a money amount.

The answer text is a fixed template filled with those numbers (see expense_ai.py): there is NO model narration in 5a, so the
model cannot put a wrong number into a sentence. `unexpected_numbers` is the tripwire for that rule: it finds every number in an
answer that is not one of the numbers the query returned. expense_ai runs it on every query answer, and it is ready for the day
a narration step is added (a narrated number that was not in the data must be refused).

Only the user_id taken from the session reaches the database. Nothing the person wrote is logged.
"""
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from datetime import date, timedelta
from typing import Optional

from app.services import expense_service as es
from app.services.expense_normalise import fold, parse_period_text

MAX_GROUP_LINES = 8


@dataclass
class QueryGroup:
    key: str
    label_en: str
    label_ur: str
    total_paisa: int
    count: int


@dataclass
class QueryResult:
    date_from: date                 # first day (inclusive)
    date_to: date                   # day after the last day (the window is [from, to))
    total_paisa: int
    count: int
    category_slug: Optional[str] = None
    category_en: Optional[str] = None
    category_ur: Optional[str] = None
    group_by: str = "none"
    groups: list = field(default_factory=list)      # at most MAX_GROUP_LINES, in the order SQL gave them
    hidden: int = 0                                 # how many more groups there were

    @property
    def last_day(self) -> date:
        return self.date_to - timedelta(days=1)


def run_query(db, user_id: int, query, message: str, today: date, rows: list):
    """(QueryResult, None) or (None, problem). Problems: period, category. `query` is expense_understand.QueryU."""
    period_text = query.period_text or ""
    if period_text and fold(period_text) not in fold(message):        # the model must have copied the words, not invented them
        return None, "period"
    window = parse_period_text(period_text, today)
    if window is None:
        return None, "period"
    date_from, date_to = window

    names = {slug: (en, ur) for slug, en, ur in rows}
    slug = query.category_slug
    if slug is not None and slug not in names:
        return None, "category"
    group_by = query.group_by if query.group_by in ("none", "category", "subcategory", "day", "month") else "none"

    try:
        if slug is not None and "." in slug:
            # one subcategory: SQL groups the group's entries by subcategory, and the line for this one is the answer
            summary = es.summarize(db, user_id, date_from=date_from, date_to=date_to, group_by="subcategory",
                                   category_slug=slug.split(".")[0])
            hit = next((g for g in summary.groups if g.key == slug), None)
            total, count, groups, group_by = (hit.total_paisa, hit.count, [], "none") if hit else (0, 0, [], "none")
        else:
            summary = es.summarize(db, user_id, date_from=date_from, date_to=date_to, group_by=group_by, category_slug=slug)
            total, count = summary.total_paisa, summary.count
            groups = summary.groups
    except es.InvalidInput:
        return None, "category"

    shown = [QueryGroup(g.key, g.label_en or g.key, g.label_ur or g.key, g.total_paisa, g.count) for g in groups[:MAX_GROUP_LINES]]
    en, ur = names[slug] if slug is not None else (None, None)
    return QueryResult(date_from=date_from, date_to=date_to, total_paisa=total, count=count, category_slug=slug,
                       category_en=en, category_ur=ur, group_by=group_by, groups=shown,
                       hidden=max(0, len(groups) - len(shown))), None


# ── the tripwire: no number in an answer that the data did not give ──────────────────────────────

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def _canonical(raw: str) -> str:
    """"1,250.50" -> "1250.5" and "01" -> "1": commas are thousands separators, the dot is the decimal point. Separators are NOT
    simply dropped: "Rs 1,250.50" and "Rs 125,050" differ by a factor of 100 and must stay different numbers."""
    plain = raw.replace(",", "")
    try:
        return format(Decimal(plain).normalize(), "f")
    except InvalidOperation:
        return plain


def numbers_in(text: str) -> set:
    """Every number in a text in canonical form (Urdu digits folded)."""
    return {_canonical(m) for m in _NUMBER.findall(fold(text))}


def unexpected_numbers(answer: str, data_text: str) -> set:
    """The numbers in `answer` that are not in `data_text` (the numbers the query returned, written out). Empty = fine."""
    return numbers_in(answer) - numbers_in(data_text)
