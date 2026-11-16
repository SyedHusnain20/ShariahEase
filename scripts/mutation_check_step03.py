"""
Mutation check for Path step 3 (Zakat / Sadaqah statement, riba and doubtful reminders, dismissal, safe wording).

For each mutation: copy the repo to a temp folder, break ONE thing in the copy, run the tests that guard it, and require
them to FAIL. A mutation that survives means a test is missing.

    python scripts/mutation_check_step03.py --check          # only verify that every mutation applies exactly once
    python scripts/mutation_check_step03.py --slice 0:20     # a range (a slow machine: run in slices)
    python scripts/mutation_check_step03.py --only statement:   # only mutations whose label contains this text

Mutations that would be EQUIVALENT (two defences give the same answer, so no test can tell them apart) are left out on
purpose; the step's README lists them.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "*.db", "venv", ".venv", ".pytest_cache", "knowledge_base", "gate_reports")

SVC = "app/services/expense_service.py"
FLAGS = "app/services/shariah_flags.py"
PDF = "app/services/statement_pdf.py"
ROUTER = "app/routers/expenses.py"
JS = "frontend/static/js/expenses.js"
HTML = "frontend/templates/pages/expenses.html"
SW = "frontend/static/sw.js"

FLAG_T = ["tests/test_shariah_flags.py", "tests/test_expense_flags_service.py"]
STMT_T = ["tests/test_charity_statement.py"]
API_T = ["tests/test_statement_api.py"]
PDF_T = ["tests/test_statement_pdf.py", "tests/test_statement_api.py"]
WORD_T = ["tests/test_shariah_wording.py", "tests/test_expense_flags_js.py", "tests/test_expense_pwa.py"]
JS_T = ["tests/test_expense_flags_js.py"]

# (file, old, new, label, tests)
M = []
def add(f, old, new, label, tests):
    M.append((f, old, new, label, tests))

# --- keyword rule ---------------------------------------------------------------------------------
add(FLAGS, '    if _STRONG.search(text):\n        return "riba"', '    if _STRONG.search(text):\n        return "doubtful"', "flags: a strong word only raises doubtful", FLAG_T)
add(FLAGS, '    if _WEAK.search(text):\n        return "doubtful"', '    if _WEAK.search(text):\n        return "riba"', "flags: markup raises riba", FLAG_T)
add(FLAGS, '    text = _NEGATION.sub(" ", normalise(note))', '    text = normalise(note)', "flags: 'interest free' still flags", FLAG_T)
add(FLAGS, 'rf"(?<![{_LETTER}])(?:{body})(?![{_LETTER}])"', 'rf"(?:{body})(?![{_LETTER}])"', "flags: a word glued after a letter matches (xinterest)", FLAG_T)
add(FLAGS, 'rf"(?<![{_LETTER}])(?:{body})(?![{_LETTER}])"', 'rf"(?<![{_LETTER}])(?:{body})"', "flags: a word glued before a letter matches (interesting)", FLAG_T)
add(FLAGS, "\u0620-\u064a\u0660-\u0669\u066e-\u06d3", "\u0600-\u064a\u0660-\u0669\u066e-\u06d3", "flags: the Arabic comma counts as a letter", FLAG_T)
add(FLAGS, "\u06d5\u06f0-\u06ff\u0750-\u077f\u200c\u200d", "\u06f0-\u06ff\u0750-\u077f\u200c\u200d", "flags: the letter heh (U+06D5) is not a letter", FLAG_T)
add(FLAGS, "    return a if _RANK[a] >= _RANK[b] else b", "    return a if _RANK[a] <= _RANK[b] else b", "flags: the weaker flag wins", FLAG_T)
add(FLAGS, "    a = a if a in _VALID else None\n", "    pass\n", "flags: an unknown flag value is trusted", FLAG_T)
add(FLAGS, "    return stronger(default_flag, keyword_flag(note))", "    return keyword_flag(note)", "flags: the subcategory default is ignored", FLAG_T)
add(FLAGS, "    return stronger(default_flag, keyword_flag(note))", "    return default_flag or keyword_flag(note)", "flags: a default blocks a stronger note", FLAG_T)
add(FLAGS, "    text = text.casefold().translate(_LETTER_MAP)", "    text = text.translate(_LETTER_MAP)", "flags: upper case is not folded", FLAG_T)
add(FLAGS, '    text = unicodedata.normalize("NFKD", text)', '    text = unicodedata.normalize("NFC", text)', "flags: no compatibility normalisation (presentation forms, accents)", FLAG_T)
add(FLAGS, 'if unicodedata.category(ch) != "Mn" and ch != _TATWEEL)', 'if ch != _TATWEEL)', "flags: vowel marks are not removed", FLAG_T)
add(FLAGS, 'if unicodedata.category(ch) != "Mn" and ch != _TATWEEL)', 'if unicodedata.category(ch) != "Mn")', "flags: tatweel is not removed", FLAG_T)
add(FLAGS, '_LETTER_MAP = str.maketrans({"\u064a": "\u06cc", "\u0649": "\u06cc", "\u0643": "\u06a9"})', '_LETTER_MAP = str.maketrans({})', "flags: Arabic yeh and kaf are not mapped", FLAG_T)
add(FLAGS, '    "interest", "interests", "riba", "ribaa", "usury",', '    "interests", "riba", "ribaa", "usury",', "flags: the word 'interest' removed from the list", FLAG_T)
add(FLAGS, '    "sood", "sud", "soodi",                      # Roman Urdu', '    "sud", "soodi",                      # Roman Urdu', "flags: the word 'sood' removed from the list", FLAG_T)
add(FLAGS, '    "سود", "سودی", "ربا", "ربوا",', '    "سودی", "ربا", "ربوا",', "flags: the Urdu word for interest removed from the list", FLAG_T)
add(FLAGS, '    "bila sood", "bila sud", "bilasood",', '    "bila sud", "bilasood",', "flags: 'bila sood' no longer a negation", FLAG_T)
add(FLAGS, '    "markup", "mark up",', '    "mark up",', "flags: 'markup' removed from the weak list", FLAG_T)
# --- flags in the service --------------------------------------------------------------------------
add(SVC, "shariah_flag=flag_for(default_flag, item.note),", "shariah_flag=default_flag,", "service: a new expense ignores its note", FLAG_T)
add(SVC, "    if cat_changed or note_changed or dismiss_given:", "    if cat_changed or dismiss_given:", "service: a note edit does not re-work the flag", FLAG_T)
add(SVC, "flag_now = flag_for(default_flag, change.note if note_changed else current.note)", "flag_now = flag_for(default_flag, None)", "service: a category change ignores the note", FLAG_T)
add(SVC, "shariah_flag=flag_now, shariah_flag_dismissed=False)", "shariah_flag=flag_now)", "service: a new category keeps the old dismissal", FLAG_T)
add(SVC, "            if flag_now != current.shariah_flag:        # a different reminder", "            if True:        # a different reminder", "service: every note edit un-dismisses", FLAG_T)
add(SVC, "            if flag_now != current.shariah_flag:        # a different reminder", "            if False:        # a different reminder", "service: a different flag after a note edit stays hidden", FLAG_T)
add(SVC, "            if change.shariah_flag_dismissed and flag_now is None:", "            if False:", "service: dismissing with no reminder is allowed", FLAG_T)
add(SVC, '            values["shariah_flag_dismissed"] = change.shariah_flag_dismissed', '            values["shariah_flag_dismissed"] = True', "service: a dismissal cannot be taken back", FLAG_T)
add(SVC, "            if current.subcategory_id is not None:", "            if False:", "service: a note edit forgets the subcategory default", FLAG_T)
add(SVC, "    model_config = ConfigDict(extra=\"forbid\")\n    category_slug: Optional[str] = None\n    subcategory_slug: Optional[str] = None\n    amount_paisa: Optional[StrictInt] = None",
    "    model_config = ConfigDict(extra=\"forbid\")\n    category_slug: Optional[str] = None\n    subcategory_slug: Optional[str] = None\n    amount_paisa: Optional[StrictInt] = None", "(placeholder)", FLAG_T) if False else None
add(SVC, "    shariah_flag_dismissed: Optional[StrictBool] = None      # True", "    shariah_flag_dismissed: Optional[bool] = None      # True", "service: dismissal accepts 1 or 'true'", FLAG_T)
# --- statement: which rows -------------------------------------------------------------------------
add(SVC, "    conditions = [Expense.user_id == user_id, Expense.deleted_at.is_(None),\n                  Expense.occurred_on >= date_from, Expense.occurred_on < date_to, is_charity]", "    conditions = [Expense.deleted_at.is_(None),\n                  Expense.occurred_on >= date_from, Expense.occurred_on < date_to, is_charity]", "statement: not scoped to the user", STMT_T + API_T)
add(SVC, "    conditions = [Expense.user_id == user_id, Expense.deleted_at.is_(None),\n                  Expense.occurred_on >= date_from, Expense.occurred_on < date_to, is_charity]", "    conditions = [Expense.user_id == user_id,\n                  Expense.occurred_on >= date_from, Expense.occurred_on < date_to, is_charity]", "statement: deleted expenses appear", STMT_T + API_T)
add(SVC, "Expense.occurred_on >= date_from, Expense.occurred_on < date_to, is_charity]", "Expense.occurred_on > date_from, Expense.occurred_on < date_to, is_charity]", "statement: first day excluded", STMT_T)
add(SVC, "Expense.occurred_on >= date_from, Expense.occurred_on < date_to, is_charity]", "Expense.occurred_on >= date_from, Expense.occurred_on <= date_to, is_charity]", "statement: end day included", STMT_T + API_T)
add(SVC, "Expense.occurred_on >= date_from, Expense.occurred_on < date_to, is_charity]", "Expense.occurred_on >= date_from, Expense.occurred_on < date_to]", "statement: every category counts", STMT_T)
add(SVC, "Expense.occurred_on >= date_from, Expense.occurred_on < date_to, is_charity]", "Expense.occurred_on >= date_from, is_charity]", "statement: no end to the window", STMT_T)
add(SVC, "Subcategory.charity_type, literal_column(\"'unspecified'\"))", "Subcategory.charity_type, literal_column(\"'zakat'\"))", "statement: untyped charity counted as Zakat", STMT_T)
add(SVC, "        if key == \"unspecified\" and n == 0:", "        if key == \"unspecified\":", "statement: the untyped line is always hidden", STMT_T)
add(SVC, "        if key == \"unspecified\" and n == 0:", "        if False:", "statement: an empty untyped line is shown", STMT_T)
add(SVC, "        .order_by(Expense.occurred_on, Expense.id).limit(entry_limit + 1)", "        .order_by(Expense.occurred_on.desc(), Expense.id).limit(entry_limit + 1)", "statement: entries newest first", STMT_T)
add(SVC, "        .order_by(Expense.occurred_on, Expense.id).limit(entry_limit + 1)", "        .order_by(Expense.occurred_on, Expense.id).limit(entry_limit)", "statement: the cut is never reported", STMT_T)
add(SVC, "    for e, key in rows[:entry_limit]:", "    for e, key in rows:", "statement: one entry over the limit is listed", STMT_T)
add(SVC, "entries_truncated=len(rows) > entry_limit,", "entries_truncated=len(rows) >= entry_limit,", "statement: an exact fit is reported as cut", STMT_T)
add(SVC, "    overall = db.execute(joined(select(total, count)).where(*conditions)).one()\n", "    overall = db.execute(joined(select(total, count)).where(*conditions).limit(1)).one()\n    overall = (sum(t[1] for t in by_type_rows), 0) if False else overall\n", "(unused)", STMT_T) if False else None
# --- statement: window checks ---------------------------------------------------------------------
add(SVC, '    if date_from > date_to:\n        raise _bad("date_from", "must not be after date_to")\n    if date_from < STATEMENT_MIN_DATE', '    if date_from >= date_to:\n        raise _bad("date_from", "must not be after date_to")\n    if date_from < STATEMENT_MIN_DATE', "statement: an empty window is refused", STMT_T)
add(SVC, '    if date_from > date_to:\n        raise _bad("date_from", "must not be after date_to")\n    if date_from < STATEMENT_MIN_DATE', '    if date_from < STATEMENT_MIN_DATE', "statement: a backwards window is allowed", STMT_T)
add(SVC, "    if date_from < STATEMENT_MIN_DATE or date_to > STATEMENT_MAX_DATE:", "    if date_to > STATEMENT_MAX_DATE:", "statement: dates before 2000 allowed", STMT_T)
add(SVC, "    if date_from < STATEMENT_MIN_DATE or date_to > STATEMENT_MAX_DATE:", "    if date_from < STATEMENT_MIN_DATE:", "statement: dates after 2200 allowed", STMT_T)
add(SVC, "    if isinstance(entry_limit, bool) or not isinstance(entry_limit, int) or not 1 <= entry_limit <= 10000:", "    if not isinstance(entry_limit, int) or not 1 <= entry_limit <= 10000:", "statement: True accepted as an entry limit", STMT_T)
add(SVC, "or not isinstance(entry_limit, int) or not 1 <= entry_limit <= 10000:", "or not isinstance(entry_limit, int) or not 0 <= entry_limit <= 10000:", "statement: an entry limit of 0 accepted", STMT_T)
# --- statement: calculator comparison -------------------------------------------------------------
add(SVC, '        paid = by_type.get("zakat", (0, 0))[0]', '        paid = int(overall[0])', "check: all giving counts as Zakat paid", STMT_T)
add(SVC, '        paid = by_type.get("zakat", (0, 0))[0]', '        paid = by_type.get("sadaqah", (0, 0))[0]', "check: Sadaqah counts as Zakat paid", STMT_T)
add(SVC, "remaining_paisa=max(0, due - paid),", "remaining_paisa=due - paid,", "check: remaining can go negative", STMT_T)
add(SVC, "overpaid_paisa=max(0, paid - due))", "overpaid_paisa=paid - due)", "check: overpaid can go negative", STMT_T)
add(SVC, "        .where(ZakatRecord.user_id == user_id, ZakatRecord.calculated_at >= lo,", "        .where(ZakatRecord.calculated_at >= lo,", "check: another user's calculation is used", STMT_T + API_T)
add(SVC, "ZakatRecord.calculated_at.desc(), ZakatRecord.id.desc()", "ZakatRecord.calculated_at.asc(), ZakatRecord.id.desc()", "check: the oldest calculation is used", STMT_T)
add(SVC, "    lo = datetime.combine(date_from - timedelta(days=1), time.min, tzinfo=utc)", "    lo = datetime.combine(date_from, time.min, tzinfo=utc)", "check: no slack before the window (zone shift)", STMT_T)
add(SVC, "    hi = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=utc)", "    hi = datetime.combine(date_to, time.min, tzinfo=utc)", "check: no slack after the window (zone shift)", STMT_T)
add(SVC, "        local = _aware(calculated_at).astimezone(zone).date()", "        local = _aware(calculated_at).astimezone(timezone.utc).date()", "check: the calculation date is taken in UTC", STMT_T)
add(SVC, "        if not date_from <= local < date_to:", "        if not date_from < local < date_to:", "check: a calculation on the first day is out", STMT_T)
add(SVC, "        if not date_from <= local < date_to:", "        if not date_from <= local <= date_to:", "check: a calculation on the end day is in", STMT_T)
# --- float to paisa -------------------------------------------------------------------------------
add(SVC, "    rupees = Decimal(str(value))", "    rupees = Decimal(value)", "money: float goes straight into Decimal (1.005)", STMT_T)
add(SVC, "rounding=ROUND_HALF_UP))", "rounding=ROUND_DOWN))", "money: rounds down", STMT_T)
add(SVC, "    if rupees < 0 or rupees > Decimal(MAX_AMOUNT_PAISA) / 100:", "    if rupees <= 0 or rupees > Decimal(MAX_AMOUNT_PAISA) / 100:", "money: zero is refused", STMT_T)
add(SVC, "    if rupees < 0 or rupees > Decimal(MAX_AMOUNT_PAISA) / 100:", "    if rupees < 0 or rupees >= Decimal(MAX_AMOUNT_PAISA) / 100:", "money: the cap itself is refused", STMT_T)
add(SVC, "    if rupees < 0 or rupees > Decimal(MAX_AMOUNT_PAISA) / 100:", "    if rupees > Decimal(MAX_AMOUNT_PAISA) / 100:", "money: a negative amount is accepted", STMT_T)
add(SVC, "    if rupees < 0 or rupees > Decimal(MAX_AMOUNT_PAISA) / 100:", "    if rupees < 0:", "money: no upper cap", STMT_T)
add(SVC, "    if isinstance(value, bool) or not isinstance(value, (int, float)):", "    if not isinstance(value, (int, float)):", "money: True accepted as an amount", STMT_T)
add(SVC, "    if isinstance(value, float) and not math.isfinite(value):", "    if False:", "money: NaN and infinity accepted", STMT_T)
# --- routes ----------------------------------------------------------------------------------------
add(ROUTER, "    wait = statement_limiter.check(user.id)", "    wait = statement_limiter.check(0)", "route: one shared statement allowance for everyone", API_T)
add(ROUTER, "    wait = statement_limiter.check(user.id)\n    if wait > 0:", "    wait = statement_limiter.check(user.id)\n    if False:", "route: no statement rate limit", API_T)
add(ROUTER, '        if date_from is not None or date_to is not None:\n            raise _fail(svc.InvalidInput([{"field": "year"', '        if False:\n            raise _fail(svc.InvalidInput([{"field": "year"', "route: year together with dates is accepted", API_T)
add(ROUTER, '        if not 2000 <= year <= 2100:\n            raise _fail(svc.InvalidInput([{"field": "year"', '        if not 2000 <= year <= 2150:\n            raise _fail(svc.InvalidInput([{"field": "year"', "route: year 2101 accepted", API_T)
add(ROUTER, '        if not 2000 <= year <= 2100:\n            raise _fail(svc.InvalidInput([{"field": "year"', '        if not 1990 <= year <= 2100:\n            raise _fail(svc.InvalidInput([{"field": "year"', "route: year 1999 accepted", API_T)
add(ROUTER, "    this_year = svc._local_today(db, user.id, svc._utcnow()).year", "    this_year = svc._local_today(db, user.id, svc._utcnow()).year + 1", "route: the default year is next year", API_T)
add(ROUTER, "    if lang not in statement_pdf.LANGS:", "    if False:", "route: any language accepted for the PDF", API_T)
add(ROUTER, 'issued_to=user.name or "",', 'issued_to="",', "route: the PDF forgets the person's name", API_T)
add(ROUTER, 'headers={"Cache-Control": "no-store"})', 'headers={})', "route: the statement JSON may be cached", API_T)
add(ROUTER, '"Cache-Control": "no-store",\n    })', '"Cache-Control": "private",\n    })', "route: the statement PDF may be cached", API_T)
add(ROUTER, 'attachment; filename="shariahease-zakat-sadaqah-', 'inline; filename="shariahease-zakat-sadaqah-', "route: the PDF opens in the browser instead of downloading", API_T)
add(ROUTER, "        label = str(start.year)", "        label = str(end.year)", "route: the file is named after the wrong year", API_T)
add(ROUTER, "    shariah_flag_dismissed: Optional[StrictBool] = None   # true", "    shariah_flag_dismissed: Optional[bool] = None   # true", "route: dismissal accepts 1 or 'true'", API_T)
add(ROUTER, "    shariah_flag_dismissed: Optional[StrictBool] = None   # true = \"I checked this\"; false = show the reminder again\n", "", "route: the dismissal field is not accepted", API_T)
# --- PDF --------------------------------------------------------------------------------------------
add(PDF, "    text = _NUMBER.sub(lambda m: _LRE + m.group(0) + _PDF, text)", "    text = text", "pdf: dates and numbers reversed in Urdu lines", PDF_T)
# Left out on purpose (equivalent): removing the .replace of the direction marks in statement_pdf.visual() changes nothing, because
# python-bidi already drops those marks itself. The replace stays as a second defence; test_the_direction_marks_never_reach_the_page guards the result.
add(PDF, 'base_dir="R" if lang == "ur" else "L")', 'base_dir="L")', "pdf: Urdu lines laid out left to right", PDF_T)
add(PDF, "    period_last = statement.date_to - timedelta(days=1)", "    period_last = statement.date_to", "pdf: the period ends on the exclusive day", PDF_T)
add(PDF, '    return f"{whole:,}.{cents:02d}"', '    return f"{whole:,}.{cents}"', "pdf: 5 paisa written as .5", PDF_T)
add(PDF, "    if lang not in LANGS:\n        raise ValueError", "    if False:\n        raise ValueError", "pdf: any language accepted", PDF_T)
add(PDF, "    if not _ARABIC.search(text):\n        return text\n    text = _NUMBER", "    text = _NUMBER", "pdf: plain text goes through the bidi step", PDF_T)
# --- wording and page ------------------------------------------------------------------------------
add(JS, "flag_riba: 'May involve riba'", "flag_riba: 'Is riba'", "wording: the riba label becomes a verdict", WORD_T)
add(JS, "They are not a religious opinion.'", "They are not a fatwa.'", "wording: 'fatwa' appears on the page", WORD_T)
add(JS, "It is not a Zakat certificate and not a religious opinion.'", "It is a Zakat certificate.'", "wording: the statement claims to be a certificate", WORD_T)
add(JS, "flag_verify: 'Please verify with a scholar.'", "flag_verify: 'This is forbidden.'", "wording: the reminder says forbidden", WORD_T)
add(JS, "flag_riba: 'May involve riba'", "flag_riba: 'May involve riba'", "(none)", WORD_T) if False else None
add(PDF, '"It is not a Zakat certificate and not a religious opinion. Please check the amounts,"', '"It is not a Zakat certificate and not a fatwa. Please check the amounts,"', "wording: 'fatwa' appears in the PDF", WORD_T)
add(PDF, '        "title": "زکوٰۃ و صدقات کا گوشوارہ",', '        "title": "زکوٰۃ و صدقات کا فتویٰ",', "wording: the Urdu PDF title says fatwa", WORD_T)
add(HTML, 'data-testid="flag-dismiss"', 'data-testid="flag-x"', "page: the dismiss button is gone", WORD_T)
add(HTML, 'x-show="hasLiveFlag(x)" style="display:none" class="flex items-center', 'x-show="true" style="display:none" class="flex items-center', "page: dismissed reminders still show", WORD_T)
add(HTML, '<a href="/zakat" class="text-primary underline min-h-[44px] inline-flex items-center" x-text="t(\'zakat_link\')" data-testid="statement-zakat-link">', '<a href="/zakat-calculator" class="text-primary underline min-h-[44px] inline-flex items-center" x-text="t(\'zakat_link\')" data-testid="statement-zakat-link">', "page: the statement's calculator link is broken", WORD_T)
add(HTML, "downloadPdf('ur')", "downloadPdf('en')", "page: the Urdu PDF button downloads English", WORD_T)
add(SW, "const VERSION = 'v2';", "const VERSION = 'v1';", "pwa: the cache version is not bumped", WORD_T)
add(JS, "hasLiveFlag(x) { return !!(x.shariah_flag && !x.shariah_flag_dismissed); }", "hasLiveFlag(x) { return !!x.shariah_flag; }", "js: a dismissed reminder is shown", JS_T)
add(JS, "hasLiveFlag(x) { return !!(x.shariah_flag && !x.shariah_flag_dismissed); }", "hasLiveFlag(x) { return !!(x.shariah_flag || x.shariah_flag_dismissed); }", "js: a reminder shows without a flag", JS_T)
add(JS, "flagText(x) { return x.shariah_flag === 'riba' ?", "flagText(x) { return x.shariah_flag === 'doubtful' ?", "js: the riba and doubtful labels are swapped", JS_T)
add(JS, "if (next > this.thisYear || next < 2000) return;", "if (next > this.thisYear + 1 || next < 2000) return;", "js: the year can go one into the future", JS_T)
add(JS, "if (next > this.thisYear || next < 2000) return;", "if (next > this.thisYear || next < 1990) return;", "js: the year can go before 2000", JS_T)
add(JS, "{ version: x.version, shariah_flag_dismissed: dismissed }", "{ version: x.version, shariah_flag_dismissed: true }", "js: showing a reminder again sends 'dismiss'", JS_T)
add(JS, "{ version: x.version, shariah_flag_dismissed: dismissed }", "{ shariah_flag_dismissed: dismissed }", "js: the dismissal sends no version", JS_T)
add(JS, "if (r.status === 409 || r.status === 404) { this.closeEdit();", "if (r.status === 404) { this.closeEdit();", "js: a stale dismissal is not reloaded", JS_T)
add(JS, "'/api/expenses/statement.pdf?year=' + this.stYear + '&lang=' + lang", "'/api/expenses/statement.pdf?year=' + this.stYear", "js: the PDF language is not sent", JS_T)
add(JS, "        } finally {\n          this.stBusy = '';", "        } finally {\n          this.stBusy = lang;", "js: the PDF buttons stay locked after a download", JS_T)
add(JS, "this.t('err_rate_st', { n: res.headers.get('Retry-After') || 60 })", "this.t('err_download')", "js: a rate-limited download is not explained", JS_T)

M = [m for m in M if m is not None and m[3] not in ("(none)", "(placeholder)", "(unused)")]


def run_tests(repo: Path, tests) -> bool:
    cmd = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "-W", "ignore", *tests]
    try:
        return subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=90).returncode == 0
    except subprocess.TimeoutExpired:           # a hang is a failure too: the broken code is noticed
        return False


def _apply(text, old, new, label):
    if text.count(old) != 1:
        return None, f"BAD MUTATION (found {text.count(old)}x): {label}"
    return text.replace(old, new), None


def main() -> int:
    check_only = "--check" in sys.argv
    lo, hi = 0, len(M)
    if "--slice" in sys.argv:
        a, b = sys.argv[sys.argv.index("--slice") + 1].split(":")
        lo, hi = int(a or 0), int(b or len(M))
    chosen = M[lo:hi]
    if "--only" in sys.argv:
        needle = sys.argv[sys.argv.index("--only") + 1]
        chosen = [m for m in chosen if needle in m[3]]
    bad = [p for rel, old, new, label, _ in M for _t, p in [_apply((ROOT / rel).read_text(encoding="utf-8"), old, new, label)] if p]
    if bad:
        print("\n".join(bad))
        return 2
    if check_only:
        print(f"all {len(M)} mutations apply exactly once")
        return 0
    survived, ran = [], 0
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "base"
        shutil.copytree(ROOT, base, ignore=IGNORE)
        (base / "knowledge_base").mkdir(exist_ok=True)
        for tests in sorted({tuple(m[4]) for m in chosen}):
            if not run_tests(base, list(tests)):
                print(f"The unmodified tests already fail: {tests}")
                return 2
        for rel, old, new, label, tests in chosen:
            work = Path(tmp) / "work"
            if work.exists():
                shutil.rmtree(work)
            shutil.copytree(base, work)
            text, _ = _apply((work / rel).read_text(encoding="utf-8"), old, new, label)
            (work / rel).write_text(text, encoding="utf-8")
            caught = not run_tests(work, tests)
            ran += 1
            print(("CAUGHT   " if caught else "SURVIVED ") + label, flush=True)
            if not caught:
                survived.append(label)
    print(f"\n{ran - len(survived)}/{ran} mutations caught")
    if survived:
        print("SURVIVORS:\n  " + "\n  ".join(survived))
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
