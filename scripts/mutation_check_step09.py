"""
Mutation check for the expense service (step 9): authorization (user_id filters), idempotency, the version check,
the soft-delete filter, the date windows, money validation and the audit/privacy rules.

For each mutation: copy the repo to a temp folder, break ONE thing in the copy, run the expense tests, and require
them to FAIL. A mutation that survives means a test is missing.

    python scripts/mutation_check_step09.py            # offline tests only; mutations that need Postgres are skipped
    python scripts/mutation_check_step09.py --check    # only verify that every mutation applies exactly once
    python scripts/mutation_check_step09.py --slice 0:20   # run only part of the list

If DATABASE_URL_TEST points at a throwaway Postgres database, the `pg` tests are included, and the mutations that
only real concurrency can catch are run too.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "*.db", "venv", ".venv", ".pytest_cache", "knowledge_base")
SVC = "app/services/expense_service.py"

# (file, old, new, label[, "pg"])  -- old/new may be lists of equal length to change several places at once
MUTATIONS = [
    # --- authorization: user_id on every function -------------------------------------------------
    (SVC, "    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 1:", "    if False:", "user_id type check removed"),
    (SVC, 'Expense.user_id == user_id, Expense.client_id == values["client_id"])).first()', 'Expense.client_id == values["client_id"])).first()', "add: retry lookup not scoped to the user"),
    (SVC, "            .where(Expense.id == expense_id, Expense.user_id == user_id,\n                   Expense.deleted_at.is_(None), Expense.version == expected_version)", "            .where(Expense.id == expense_id,\n                   Expense.deleted_at.is_(None), Expense.version == expected_version)", "update: row not scoped to the user"),
    (SVC, "                Expense.id == expense_id, Expense.user_id == user_id, Expense.deleted_at.is_(None))).scalar()", "                Expense.id == expense_id, Expense.deleted_at.is_(None))).scalar()", "update: version of another user's row revealed"),
    (SVC, "            .where(Expense.user_id == user_id, Expense.id.in_(ids), state)", "            .where(Expense.id.in_(ids), state)", "delete/restore: not scoped to the user"),
    (SVC, "    conditions = [Expense.user_id == user_id, Expense.deleted_at.is_(None)]\n    if date_from is not None:", "    conditions = [Expense.deleted_at.is_(None)]\n    if date_from is not None:", "get: list not scoped to the user"),
    (SVC, "    conditions = [Expense.user_id == user_id, Expense.deleted_at.is_(None),\n                  Expense.occurred_on >= date_from, Expense.occurred_on < date_to]", "    conditions = [Expense.deleted_at.is_(None),\n                  Expense.occurred_on >= date_from, Expense.occurred_on < date_to]", "summarize: not scoped to the user"),
    (SVC, 'class ExpenseIn(BaseModel):\n    """One expense to create. There is deliberately no user_id here."""\n    model_config = ConfigDict(extra="forbid")', 'class ExpenseIn(BaseModel):\n    """One expense to create. There is deliberately no user_id here."""\n    model_config = ConfigDict(extra="ignore")', "create input accepts extra fields (a user_id)"),
    (SVC, 'class ExpenseUpdate(BaseModel):\n    """Fields to change. Only fields that are present are changed; `note: null` clears the note."""\n    model_config = ConfigDict(extra="forbid")', 'class ExpenseUpdate(BaseModel):\n    """Fields to change. Only fields that are present are changed; `note: null` clears the note."""\n    model_config = ConfigDict(extra="ignore")', "update input accepts extra fields"),
    # --- soft delete -----------------------------------------------------------------------------
    (SVC, "    conditions = [Expense.user_id == user_id, Expense.deleted_at.is_(None)]\n    if date_from is not None:", "    conditions = [Expense.user_id == user_id]\n    if date_from is not None:", "get: deleted rows are listed"),
    (SVC, "    conditions = [Expense.user_id == user_id, Expense.deleted_at.is_(None),\n                  Expense.occurred_on >= date_from", "    conditions = [Expense.user_id == user_id,\n                  Expense.occurred_on >= date_from", "summarize: deleted rows are counted"),
    (SVC, "                   Expense.deleted_at.is_(None), Expense.version == expected_version)", "                   Expense.version == expected_version)", "update: a deleted expense can be edited"),
    (SVC, "    state = Expense.deleted_at.is_(None) if deleting else Expense.deleted_at.is_not(None)", "    state = Expense.id > 0", "delete/restore: current state not checked"),
    (SVC, "            if earlier is not None and earlier.deleted_at is None:", "            if earlier is not None:", "add: a retry of a deleted expense is not reported as deleted"),
    # --- idempotency -----------------------------------------------------------------------------
    (SVC, '    return insert(Expense).on_conflict_do_nothing(index_elements=["user_id", "client_id"])', "    return insert(Expense)", "add: duplicate client_id not ignored"),
    # --- optimistic version ----------------------------------------------------------------------
    (SVC, "                   Expense.deleted_at.is_(None), Expense.version == expected_version)", "                   Expense.deleted_at.is_(None))", "update: version not checked"),
    (SVC, "    values.update(updated_at=now, version=Expense.version + 1)", "    values.update(updated_at=now)", "update: version not bumped"),
    (SVC, "            .values(deleted_at=now if deleting else None, updated_at=now, version=Expense.version + 1)", "            .values(deleted_at=now if deleting else None, updated_at=now)", "delete/restore: version not bumped"),
    (SVC, "            raise VersionConflict(current)", '            raise NotFound("expense not found")', "update: stale version reported as not found"),
    ([SVC, SVC], ["                   Expense.deleted_at.is_(None), Expense.version == expected_version)", "        new_version = db.execute(stmt).scalar()\n        if new_version is None:"],
     ["                   Expense.deleted_at.is_(None))", "        seen = db.execute(select(Expense.version).where(Expense.id == expense_id, Expense.user_id == user_id)).scalar()\n        if seen is not None and seen != expected_version:\n            raise VersionConflict(seen)\n        new_version = db.execute(stmt).scalar()\n        if new_version is None:"],
     "update: version checked by read-then-write (race)", "pg"),
    (SVC, "    if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 1:", "    if False:", "expected_version not validated"),
    # --- money -----------------------------------------------------------------------------------
    (SVC, "    amount_paisa: StrictInt = Field(ge=1, le=MAX_AMOUNT_PAISA)", "    amount_paisa: StrictInt = Field(ge=0, le=MAX_AMOUNT_PAISA)", "create: zero amount allowed"),
    (SVC, "    amount_paisa: StrictInt = Field(ge=1, le=MAX_AMOUNT_PAISA)", "    amount_paisa: StrictInt = Field(ge=1, le=MAX_AMOUNT_PAISA * 10)", "create: amount above the cap allowed"),
    (SVC, "    amount_paisa: StrictInt = Field(ge=1, le=MAX_AMOUNT_PAISA)", "    amount_paisa: int = Field(ge=1, le=MAX_AMOUNT_PAISA)", "create: text and booleans accepted as amounts"),
    (SVC, "    amount_paisa: Optional[StrictInt] = Field(default=None, ge=1, le=MAX_AMOUNT_PAISA)", "    amount_paisa: Optional[int] = Field(default=None, ge=1, le=MAX_AMOUNT_PAISA)", "update: text and booleans accepted as amounts"),
    (SVC, "    amount_paisa: Optional[StrictInt] = Field(default=None, ge=1, le=MAX_AMOUNT_PAISA)", "    amount_paisa: Optional[StrictInt] = Field(default=None, ge=0, le=MAX_AMOUNT_PAISA)", "update: zero amount allowed"),
    (SVC, "    total = cast(func.coalesce(func.sum(Expense.amount_paisa), 0), BigInteger)", "    total = cast(func.coalesce(func.max(Expense.amount_paisa), 0), BigInteger)", "summarize: total is not the sum"),
    (SVC, "    total = cast(func.coalesce(func.sum(Expense.amount_paisa), 0), BigInteger)", "    total = cast(func.coalesce(func.sum(Expense.amount_paisa), 0), Integer)", "summarize: totals cut to 32 bits", "pg"),
    # --- dates and windows -----------------------------------------------------------------------
    (SVC, "    if value > today + timedelta(days=MAX_FUTURE_DAYS):", "    if False:", "future dates allowed"),
    (SVC, "    return now.astimezone(tz).date()", "    return now.date()", "today taken from the server clock, not the user's time zone"),
    (SVC, "MAX_FUTURE_DAYS = 1 ", "MAX_FUTURE_DAYS = 0 ", "no allowance for tomorrow"),
    (SVC, "MAX_FUTURE_DAYS = 1 ", "MAX_FUTURE_DAYS = 30 ", "a month ahead allowed"),
    (SVC, "    except (ZoneInfoNotFoundError, ValueError):", "    except ZoneInfoNotFoundError:", "malformed time zone crashes instead of falling back"),
    (SVC, '        conditions.append(Expense.occurred_on < _check_date("date_to", date_to))', '        conditions.append(Expense.occurred_on <= _check_date("date_to", date_to))', "get: window end inclusive"),
    (SVC, '        conditions.append(Expense.occurred_on >= _check_date("date_from", date_from))', '        conditions.append(Expense.occurred_on > _check_date("date_from", date_from))', "get: window start exclusive"),
    (SVC, "Expense.occurred_on >= date_from, Expense.occurred_on < date_to]", "Expense.occurred_on >= date_from, Expense.occurred_on <= date_to]", "summarize: window end inclusive"),
    (SVC, "Expense.occurred_on >= date_from, Expense.occurred_on < date_to]", "Expense.occurred_on > date_from, Expense.occurred_on < date_to]", "summarize: window start exclusive"),
    (SVC, "and_(Expense.occurred_on == b_date, Expense.id < b_id)", "and_(Expense.occurred_on == b_date, Expense.id <= b_id)", "paging repeats the last row"),
    (SVC, "or_(Expense.occurred_on < b_date,", "or_(Expense.occurred_on <= b_date,", "paging repeats rows of the cursor date"),
    (SVC, "        .order_by(Expense.occurred_on.desc(), Expense.id.desc()).limit(limit + 1)", "        .order_by(Expense.occurred_on.asc(), Expense.id.desc()).limit(limit + 1)", "list is not newest first"),
    (SVC, "        .order_by(Expense.occurred_on.desc(), Expense.id.desc()).limit(limit + 1)", "        .order_by(Expense.occurred_on.desc(), Expense.id.desc()).limit(limit)", "paging never reports a next page"),
    # --- categories and flags ----------------------------------------------------------------------
    (SVC, "        category_id, _sub, _flag = _Taxonomy(db).resolve(category_slug, None, \"\")\n        conditions.append(Expense.category_id == category_id)", "        category_id, _sub, _flag = _Taxonomy(db).resolve(category_slug, None, \"\")", "summarize: category filter ignored"),
    (SVC, "        conditions.append(Expense.category_id == category_id)\n        if sub_id is not None:", "        if sub_id is not None:", "get: category filter ignored"),
    (SVC, "            conditions.append(Expense.subcategory_id == sub_id)", "            pass", "get: subcategory filter ignored"),
    (SVC, "            if category_slug is not None and self.category_slug_by_id.get(sub.category_id) != category_slug:", "            if False:", "a subcategory of another category is accepted"),
    (SVC, "source=source, shariah_flag=flag, created_at=now", "source=source, shariah_flag=None, created_at=now", "add: default riba/doubtful flag not set"),
    (SVC, "values.update(category_id=category_id, subcategory_id=sub_id, shariah_flag=flag, shariah_flag_dismissed=False)", "values.update(category_id=category_id, subcategory_id=sub_id, shariah_flag_dismissed=False)", "update: flag not refreshed when the subcategory changes"),
    (SVC, "values.update(category_id=category_id, subcategory_id=sub_id, shariah_flag=flag, shariah_flag_dismissed=False)", "values.update(category_id=category_id, subcategory_id=sub_id, shariah_flag=flag, shariah_flag_dismissed=True)", "update: a changed subcategory arrives with the flag dismissed"),
    # --- limits, transactions, audit and privacy ---------------------------------------------------
    (SVC, "    if len(items) > MAX_ITEMS_PER_ADD:", "    if False:", "no limit on items per call"),
    (SVC, "    if len(out) > MAX_IDS_PER_CALL:", "    if False:", "no limit on ids per call"),
    (SVC, "        if commit:\n            db.rollback()\n        raise", "        raise", "a failed call is not rolled back"),
    (SVC, "        db.commit() if commit else db.flush()", "        db.flush()", "service never commits"),
    (SVC, "    if detail and not set(detail) <= _AUDIT_DETAIL_KEYS:", "    if False:", "audit accepts any detail keys"),
    ([SVC, SVC], ['frozenset({"source", "fields", "version", "count"})', '_audit(db, user_id, actor, "expense.create", new_id, {"source": source})'],
     ['frozenset({"source", "fields", "version", "count", "note"})', '_audit(db, user_id, actor, "expense.create", new_id, {"source": source, "note": values["note"]})'], "note text written to the audit log"),
    (SVC, '"problem": err["type"]}', '"problem": str(err.get("input"))}', "validation errors echo the user's text"),
    (SVC, "        if ch in _BIDI_CONTROLS:\n            continue", "        if False:\n            continue", "text-direction override characters kept in notes"),
    (SVC, '        elif unicodedata.category(ch) == "Cc":\n            continue', '        elif False:\n            continue', "control characters kept in notes"),
    (SVC, "    if len(text) > MAX_NOTE_CHARS:", "    if False:", "note length not checked by the service"),
    (SVC, "    text = unicodedata.normalize(\"NFC\", value)", "    text = value", "notes not normalised"),
    (SVC, '    if actor not in _ACTORS:', '    if False:', "unknown actors accepted"),
    (SVC, "        raise _bad(\"source\", \"unknown source\")", "        pass", "unknown source accepted"),
]
MUTATIONS = [m for m in MUTATIONS if m is not None]


def run_tests(repo: Path, with_pg: bool) -> bool:
    cmd = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "tests/test_expense_schema.py",
           "tests/test_expense_write.py", "tests/test_expense_read.py", "tests/test_expense_authorization.py",
           "tests/test_expense_privacy.py"]
    if with_pg:
        cmd += ["tests/test_expense_pg.py", "-m", "not live"]
    try:
        return subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=240).returncode == 0
    except subprocess.TimeoutExpired:          # a hang is a failure too: the broken code is noticed
        return False


def _apply(text: str, old, new, label: str):
    olds, news = (old, new) if isinstance(old, list) else ([old], [new])
    for o, n in zip(olds, news):
        if text.count(o) != 1:
            return None, f"BAD MUTATION (found {text.count(o)}x): {label}"
        text = text.replace(o, n)
    return text, None


def main() -> int:
    check_only = "--check" in sys.argv
    lo, hi = 0, len(MUTATIONS)
    if "--slice" in sys.argv:                                   # --slice 20:40 runs mutations 20..39 (for time-limited shells)
        a, b = sys.argv[sys.argv.index("--slice") + 1].split(":")
        lo, hi = int(a or 0), int(b or len(MUTATIONS))
    with_pg = bool(os.getenv("DATABASE_URL_TEST"))
    bad = []
    for m in MUTATIONS:
        rel, old, new, label = m[:4]
        rel = rel[0] if isinstance(rel, list) else rel
        _, problem = _apply((ROOT / rel).read_text(encoding="utf-8"), old, new, label)
        if problem:
            bad.append(problem)
    if bad:
        print("\n".join(bad))
        return 2
    if check_only:
        print(f"all {len(MUTATIONS)} mutations apply exactly once")
        return 0

    survived, skipped, ran = [], [], 0
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "base"
        shutil.copytree(ROOT, base, ignore=IGNORE)
        (base / "knowledge_base").mkdir(exist_ok=True)
        if not run_tests(base, with_pg):
            print("The unmodified tests already fail; fix that first.")
            return 2
        for m in MUTATIONS[lo:hi]:
            rel, old, new, label = m[:4]
            needs_pg = len(m) > 4 and m[4] == "pg"
            if needs_pg and not with_pg:
                skipped.append(label)
                print("SKIPPED  (needs Postgres) " + label)
                continue
            work = Path(tmp) / "work"
            if work.exists():
                shutil.rmtree(work)
            shutil.copytree(base, work)
            rel = rel[0] if isinstance(rel, list) else rel
            text, _ = _apply((work / rel).read_text(encoding="utf-8"), old, new, label)
            (work / rel).write_text(text, encoding="utf-8")
            caught = not run_tests(work, with_pg)
            ran += 1
            print(("CAUGHT   " if caught else "SURVIVED ") + label)
            if not caught:
                survived.append(label)
    print(f"\n{ran - len(survived)}/{ran} mutations caught" + (f"  ({len(skipped)} skipped: need Postgres)" if skipped else ""))
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
