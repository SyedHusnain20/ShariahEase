"""
Mutation check for the export fix: the data export is six plain fields, current (not deleted) rows only, the caller's
rows only, oldest first, in batches, and the note and payment method are never read.

For each mutation: copy the repo to a temp folder, break ONE thing in the copy, run the export tests, and require
them to FAIL. A mutation that survives means a test is missing.

    python scripts/mutation_check_export_fix.py            # all mutations
    python scripts/mutation_check_export_fix.py --check    # only verify that every mutation applies exactly once
    python scripts/mutation_check_export_fix.py --only deleted
"""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "*.db", "venv", ".venv", ".pytest_cache", "knowledge_base",
                                "results", "node_modules", "UI_components", "_step*", "backups")
SVC = "app/services/expense_service.py"
FMT = "app/services/export_format.py"
RTR = "app/routers/expenses.py"
TESTS = ["tests/test_export.py", "tests/test_expense_flags_service.py", "tests/test_purge.py"]

# (file, old, new, label)
MUTATIONS = [
    # --- whose rows -------------------------------------------------------------------------------
    (SVC, "        conditions = [Expense.user_id == user_id, Expense.deleted_at.is_(None)]",
          "        conditions = [Expense.deleted_at.is_(None)]", "service: other users' rows are read"),
    (SVC, "        conditions = [Expense.user_id == user_id, Expense.deleted_at.is_(None)]",
          "        conditions = [Expense.user_id != user_id, Expense.deleted_at.is_(None)]", "service: only other users' rows are read"),
    (SVC, "    _check_user_id(user_id)\n    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:\n        raise _bad(\"batch_size\", \"must be a positive integer\")\n    last = None\n    while True:\n        conditions = [Expense.user_id == user_id, Expense.deleted_at.is_(None)]",
          "    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:\n        raise _bad(\"batch_size\", \"must be a positive integer\")\n    last = None\n    while True:\n        conditions = [Expense.user_id == user_id, Expense.deleted_at.is_(None)]",
          "service: user id is not checked"),
    # --- deleted rows -----------------------------------------------------------------------------
    (SVC, "        conditions = [Expense.user_id == user_id, Expense.deleted_at.is_(None)]",
          "        conditions = [Expense.user_id == user_id]", "service: deleted rows are exported"),
    (SVC, "        conditions = [Expense.user_id == user_id, Expense.deleted_at.is_(None)]",
          "        conditions = [Expense.user_id == user_id, Expense.deleted_at.isnot(None)]", "service: only deleted rows are exported"),
    # --- the six fields ---------------------------------------------------------------------------
    (SVC, "            select(Expense.id, Expense.occurred_on, Expense.amount_paisa, Expense.source,\n                   Category.name_en, Subcategory.name_en)",
          "            select(Expense.id, Expense.occurred_on, Expense.amount_paisa, Expense.source,\n                   Category.slug, Subcategory.name_en)", "service: category is a code, not a name"),
    (SVC, "            select(Expense.id, Expense.occurred_on, Expense.amount_paisa, Expense.source,\n                   Category.name_en, Subcategory.name_en)",
          "            select(Expense.id, Expense.occurred_on, Expense.amount_paisa, Expense.source,\n                   Category.name_en, Subcategory.slug)", "service: sub-category is a code, not a name"),
    (SVC, "            select(Expense.id, Expense.occurred_on, Expense.amount_paisa, Expense.source,\n                   Category.name_en, Subcategory.name_en)\n            .join(Category, Category.id == Expense.category_id)\n            .outerjoin(Subcategory,",
          "            select(Expense.id, Expense.occurred_on, Expense.amount_paisa, Expense.source,\n                   Category.name_en, Subcategory.name_en)\n            .join(Category, Category.id == Expense.category_id)\n            .join(Subcategory,", "service: expenses without a sub-category disappear"),
    (SVC, "            select(Expense.id, Expense.occurred_on, Expense.amount_paisa, Expense.source,\n                   Category.name_en, Subcategory.name_en)",
          "            select(Expense, Category.name_en, Subcategory.name_en)", "service: the whole row (note included) is read"),
    (SVC, "    sub_category: Optional[str]\n    source: str", "    sub_category: Optional[str]\n    source: str\n    note: Optional[str] = None", "service: export row gains a note field"),
    (SVC, "    sub_category: Optional[str]\n    source: str", "    sub_category: Optional[str]\n    source: str\n    shariah_flag: Optional[str] = None", "service: export row gains a flag field"),
    (SVC, "amount_paisa=int(amount_paisa),", "amount_paisa=int(amount_paisa) + 1,", "service: amount changed"),
    (SVC, "yield ExportRow(id=expense_id, occurred_on=occurred_on,", "yield ExportRow(id=expense_id, occurred_on=occurred_on.replace(day=1),", "service: date changed"),
    # --- order and batches ------------------------------------------------------------------------
    (SVC, "            .order_by(Expense.occurred_on, Expense.id).limit(batch_size)\n        ).all()\n        for expense_id,",
          "            .order_by(Expense.id.desc()).limit(batch_size)\n        ).all()\n        for expense_id,", "service: not oldest first"),
    (SVC, "            conditions.append(or_(Expense.occurred_on > last[0],\n                                  and_(Expense.occurred_on == last[0], Expense.id > last[1])))\n        rows = db.execute(\n            select(Expense.id,",
          "            conditions.append(Expense.occurred_on > last[0])\n        rows = db.execute(\n            select(Expense.id,", "service: rows sharing a date at a batch edge are lost"),
    (SVC, "            conditions.append(or_(Expense.occurred_on > last[0],\n                                  and_(Expense.occurred_on == last[0], Expense.id > last[1])))\n        rows = db.execute(\n            select(Expense.id,",
          "            conditions.append(or_(Expense.occurred_on >= last[0],\n                                  and_(Expense.occurred_on == last[0], Expense.id > last[1])))\n        rows = db.execute(\n            select(Expense.id,", "service: rows repeat between batches"),
    (SVC, "        if len(rows) < batch_size:\n            return\n        last = (rows[-1][1], rows[-1][0])", "        if len(rows) < batch_size:\n            return\n        last = (rows[-1][1], rows[-1][0] + 1)", "service: next batch skips a row"),
    (SVC, "        if len(rows) < batch_size:\n            return\n        last =", "        if len(rows) <= batch_size:\n            return\n        last =", "service: stops after the first full batch"),
    (SVC, "    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:", "    if isinstance(batch_size, bool) or not isinstance(batch_size, int):", "service: batch size 0 or negative accepted"),
    # --- file format ------------------------------------------------------------------------------
    (FMT, 'CSV_HEADERS = ["ID", "Date", "Amount (Rs)", "Category", "Sub-category", "Source"]', 'CSV_HEADERS = ["ID", "Date", "Amount (Rs)", "Category", "Sub-category"]', "format: a CSV column is missing"),
    (FMT, '    "ai_chat": "chatbot",', '    "ai_chat": "manual",', "format: chatbot rows labelled manual"),
    (FMT, '    return f"{paisa // 100}.{paisa % 100:02d}"', '    return f"{paisa / 100:.2f}"', "format: money goes through a float"),
    (FMT, '    probe = value.lstrip(" \\u00a0")\n    if value.startswith(_FORMULA_START) or probe.startswith(_FORMULA_START):', '    probe = value.lstrip(" \\u00a0")\n    if value.startswith(_FORMULA_START):', "format: formula after leading spaces is not protected"),
    (FMT, '    return BOM + out.getvalue()', '    return out.getvalue()', "format: no byte-order mark for Excel"),
    # --- route ------------------------------------------------------------------------------------
    (RTR, '"Cache-Control": "no-store",                            # personal data', '"Cache-Control": "public",                              # personal data', "route: export may be cached"),
    (RTR, "    rows = list(svc.export_expenses(db, user.id))", "    rows = list(svc.export_expenses(db, user.id + 1))", "route: exports another user's rows"),
]


def _make_writable_and_retry(func, path, _exc_info):
    """Windows: folders copied from a read-only (or OneDrive) source keep that flag and rmtree refuses them."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def rmtree(path) -> None:
    shutil.rmtree(path, onerror=_make_writable_and_retry)


def run_tests(repo: Path) -> bool:
    cmd = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *TESTS]
    try:
        return subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=300).returncode == 0
    except subprocess.TimeoutExpired:          # a hang is a failure too: the broken code is noticed
        return False


def _apply(text: str, old: str, new: str, label: str):
    if text.count(old) != 1:
        return None, f"BAD MUTATION (found {text.count(old)}x): {label}"
    return text.replace(old, new), None


def main() -> int:
    check_only = "--check" in sys.argv
    chosen = MUTATIONS
    if "--only" in sys.argv:
        needle = sys.argv[sys.argv.index("--only") + 1]
        chosen = [m for m in MUTATIONS if needle in m[3]]
    bad = []
    for rel, old, new, label in MUTATIONS:
        _, problem = _apply((ROOT / rel).read_text(encoding="utf-8"), old, new, label)
        if problem:
            bad.append(problem)
    if bad:
        print("\n".join(bad))
        return 2
    if check_only:
        print(f"all {len(MUTATIONS)} mutations apply exactly once")
        return 0

    survived, ran = [], 0
    tmp = tempfile.mkdtemp(prefix="mutexp_")
    try:
        base = Path(tmp) / "base"
        shutil.copytree(ROOT, base, ignore=IGNORE)
        (base / "knowledge_base").mkdir(exist_ok=True)
        if not run_tests(base):
            print("The unmodified tests already fail; fix that first.")
            return 2
        for rel, old, new, label in chosen:
            work = Path(tmp) / "work"
            if work.exists():
                rmtree(work)
            shutil.copytree(base, work)
            text, _ = _apply((work / rel).read_text(encoding="utf-8"), old, new, label)
            (work / rel).write_text(text, encoding="utf-8")
            caught = not run_tests(work)
            ran += 1
            print(("CAUGHT   " if caught else "SURVIVED ") + label, flush=True)
            if not caught:
                survived.append(label)
    finally:
        try:
            rmtree(tmp)
        except OSError:
            print(f"(could not delete the temporary folder {tmp}; you can delete it by hand)")
    print(f"\n{ran - len(survived)}/{ran} mutations caught")
    if survived:
        print("Survivors:\n  " + "\n  ".join(survived))
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
