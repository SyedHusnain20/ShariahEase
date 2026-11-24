"""
Changing or deleting an existing entry from the chat (Path step 5a): who is meant, and what exactly changes.

The model only copies words (the words that point at the entry, and the new amount or category). Code decides everything else:

  * every word the model copied into `target_text` must literally be in the person's message,
  * the target words are read by code into plain criteria (the word "last", one amount, one date, a few keywords),
  * the entries are looked up for THIS user only (user_id comes from the session; the model never names a person), in the 200 most
    recent entries by date, or on the one day the person named,
  * more than one entry that fits means the person must pick one by number. The assistant never guesses between entries.
    A single entry is chosen for the person only when every word they gave fitted it; if words had to be ignored to find
    anything, the person still has to pick it,
  * "all", "everything", "sab" and similar words are refused: one entry at a time,
  * a new amount is cross-checked against the message and read by the strict money gate, like a new expense.

Nothing is changed here. The result is the payload of a confirmation card (pending_actions.EditPayload); only the person's "yes"
changes anything, and only if the entry is still exactly as the card showed it.
"""
import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

from app.services import expense_service as es, pending_actions as pa
from app.services.expense_normalise import DateError, _MULTIPLIERS, amount_in_message, fold, normalise_amount, parse_date_text
from app.services.money import MoneyError

POOL = es.MAX_PAGE_SIZE                    # how many recent entries are searched

_LAST = frozenset({"last", "latest", "previous", "recent", "pichli", "pichla", "akhri", "aakhri", "akhiri", "آخری", "پچھلی", "پچھلا"})
_TIME_UNITS = frozenset({"month", "week", "year", "night", "mahine", "mahina", "hafte", "hafta", "saal", "ماہ", "مہینے", "ہفتے", "سال"})
_BULK = frozenset({"all", "every", "everything", "everyone", "sab", "sabhi", "sare", "saray", "saaray", "tamam", "سب", "تمام", "سارے", "سبھی"})
_STOP = frozenset("""
a an the my me i this that these those it its of on in at for to and or but with from into as be by is are was were
entry entries expense expenses record records item items one
wali wala wale waali waala ki ka ke ko se me mein ne hai hain tha thi
change edit update modify correct fix make set delete remove cancel erase undo
kar karo karna kardo dein dena de do please plz pls zara should
amount price cost value rs pkr rupay rupaye rupees rupee paisa
spent paid pay bought kharcha kharch kharche kharchay diya diye liya liye
woh wo yeh ye voh isko usko uska uski
والی والا والے کی کا کے کو سے میں ہے ہیں تھا تھی اندراج اندراجات خرچ خرچہ حذف ہٹا ہٹاؤ کر کرو کریں دو دیں دیجیے
براہ مہربانی روپے رقم یہ وہ اس اسے
""".split())
_EDGE = ".,;:!?؟،۔()[]{}\"'«»"
_POSSESSIVE = re.compile(r"['’]s$")


def tokens_of(text) -> list:
    """Folded words with the punctuation around them removed ("yesterday's" gives "yesterday")."""
    out = []
    for raw in fold(text).split(" "):
        tok = _POSSESSIVE.sub("", raw.strip(_EDGE))
        if tok:
            out.append(tok)
    return out


@dataclass
class Criteria:
    last: bool = False
    amount: Optional[int] = None            # paisa
    day: Optional[object] = None            # a date
    words: list = field(default_factory=list)
    many: bool = False                      # two amounts or two dates: the target is not clear

    @property
    def empty(self) -> bool:
        return not (self.last or self.amount is not None or self.day is not None or self.words)


def _amount_like(window: list) -> bool:
    return any(ch.isdigit() for t in window for ch in t) or any(t in _MULTIPLIERS for t in window)


def criteria_from(tokens: list, today) -> Criteria:
    """Read the target words into criteria. Dates first, then amounts, then the word "last", then keywords."""
    crit, used = Criteria(), set()
    dates, amounts = [], []

    i = 0
    while i < len(tokens):                                           # dates: "kal", "yesterday", "3 din pehle", "2026-11-19"
        for n in (3, 2, 1):
            if i + n > len(tokens):
                continue
            try:
                found = parse_date_text(" ".join(tokens[i:i + n]), today, max_future_days=0)
            except DateError:
                continue
            dates.append(found)
            used.update(range(i, i + n))
            i += n
            break
        else:
            i += 1

    i = 0
    while i < len(tokens):                                           # amounts: "3000", "Rs 500", "2 hazar", "5k"
        if i in used:
            i += 1
            continue
        for n in (3, 2, 1):
            span = range(i, i + n)
            if i + n > len(tokens) or any(j in used for j in span) or not _amount_like(tokens[i:i + n]):
                continue
            try:
                paisa = normalise_amount(" ".join(tokens[i:i + n]))
            except MoneyError:
                continue
            amounts.append(paisa)
            used.update(span)
            i += n
            break
        else:
            i += 1

    for j, tok in enumerate(tokens):                                 # "last entry": not "last month"
        if j not in used and tok in _LAST and not (j + 1 < len(tokens) and tokens[j + 1] in _TIME_UNITS):
            crit.last = True
            used.add(j)

    for j, tok in enumerate(tokens):
        if j in used or tok in _STOP or tok in _LAST or tok in _TIME_UNITS or tok.isdigit():
            continue
        if len(tok) >= 3 or (len(tok) >= 2 and re.search(r"[؀-ۿ]", tok)):
            crit.words.append(tok)

    crit.many = len(set(amounts)) > 1 or len(set(dates)) > 1
    crit.amount = amounts[0] if amounts else None
    crit.day = dates[0] if dates else None
    return crit


def _haystack(e, names: dict) -> str:
    """What a keyword may match: the entry's own note and its category names. Folded."""
    slug = e.subcategory_slug or e.category_slug
    en, ur = names.get(slug, (slug, slug))
    return fold(" ".join([e.note or "", en, ur, slug.split(".")[-1].replace("_", " ")]))


def find_candidates(db, user_id: int, crit: Criteria, rows: list):
    """(entries, truncated, relaxed). Newest first. `relaxed` = nothing fitted every word, so the words were ignored."""
    names = {slug: (en, ur) for slug, en, ur in rows}
    kwargs = {}
    if crit.day is not None:
        kwargs = {"date_from": crit.day, "date_to": crit.day + timedelta(days=1)}
    pool = es.get_expenses(db, user_id, limit=POOL, **kwargs).items

    def fits(e, use_words):
        if crit.amount is not None and e.amount_paisa != crit.amount:
            return False
        return not use_words or all(w in _haystack(e, names) for w in crit.words)

    relaxed = False
    matches = [e for e in pool if fits(e, True)]
    if not matches and crit.words and (crit.amount is not None or crit.day is not None or crit.last):
        matches, relaxed = [e for e in pool if fits(e, False)], True
    if crit.last and matches:                                        # the one added most recently
        matches = [max(matches, key=lambda e: (e.created_at, e.id))]
    return matches[:pa.MAX_CANDIDATES], len(matches) > pa.MAX_CANDIDATES, relaxed


def _snapshot(e) -> pa.EditTarget:
    note = (e.note or "")[:pa.MAX_NOTE_CHARS] or None
    return pa.EditTarget(expense_id=e.id, version=e.version, amount_paisa=e.amount_paisa,
                         category_slug=e.subcategory_slug or e.category_slug, occurred_on=e.occurred_on,
                         note=note, payment_method=e.payment_method)


@dataclass
class EditOutcome:
    problem: Optional[str] = None           # a stable word: need_change, one_at_a_time, unclear_target, amount, category, no_match
    payload: Optional[pa.EditPayload] = None
    truncated: bool = False                 # more entries fitted than the card shows


def build_edit(db, user_id: int, understood, message: str, lang: str, today, rows: list) -> EditOutcome:
    """Turn what the model copied into a card payload, or say why not. Never changes anything."""
    edit = understood.edit
    if edit.action not in ("update", "delete"):
        return EditOutcome("need_change")
    message_words = set(tokens_of(message))
    if message_words & _BULK:
        return EditOutcome("one_at_a_time")
    target_words = tokens_of(edit.target_text or "")
    if not set(target_words) <= message_words:                      # the model must have copied the words, not made some up
        return EditOutcome("unclear_target")
    crit = criteria_from(target_words, today)
    if crit.many:
        return EditOutcome("unclear_target")

    changes = None
    if edit.action == "update":
        new_amount = None
        if edit.new_amount_text:
            if not amount_in_message(edit.new_amount_text, message):
                return EditOutcome("amount")
            try:
                new_amount = normalise_amount(edit.new_amount_text)
            except MoneyError:
                return EditOutcome("amount")
        new_category = edit.new_category_slug
        if new_category is not None and new_category not in {r[0] for r in rows}:
            return EditOutcome("category")
        if new_amount is None and new_category is None:
            return EditOutcome("need_change")
        changes = pa.EditChanges(new_amount_paisa=new_amount, new_category_slug=new_category)

    found, truncated, relaxed = find_candidates(db, user_id, crit, rows)
    if not found:
        return EditOutcome("no_match")
    chosen = 0 if len(found) == 1 and not relaxed and not crit.empty else None
    payload = pa.EditPayload(lang=lang, action=edit.action, candidates=[_snapshot(e) for e in found], chosen=chosen, changes=changes)
    return EditOutcome(payload=payload, truncated=truncated)
