"""
Mutation check for Path step 2 (export, account deletion, purge, privacy, gate report, release checklist).

For each mutation: copy the repo to a temp folder, break ONE thing in the copy, run the tests that guard it, and require
them to FAIL. A mutation that survives means a test is missing. Mutations marked PG need a real PostgreSQL: set
DATABASE_URL_TEST to a throwaway database (name containing "test"); without it they are reported SKIPPED, never "caught".

    python scripts/mutation_check_step02.py --check          # only verify that every mutation applies exactly once
    python scripts/mutation_check_step02.py --slice 0:12     # a range (a slow machine: run in slices)
    python scripts/mutation_check_step02.py --only gate:     # only mutations whose label contains this text
    python scripts/mutation_check_step02.py --no-pg          # skip the PG mutations on purpose
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "*.db", "venv", ".venv", ".pytest_cache", "knowledge_base", "gate_reports")

SVC = "app/services/expense_service.py"
FMT = "app/services/export_format.py"
ROUTER = "app/routers/expenses.py"
AUTH = "app/routers/auth.py"
MODELS = "app/database/models.py"
PAGES = "app/routers/pages.py"
PRIV = "frontend/templates/pages/privacy.html"
SETJS = "frontend/static/js/settings.js"
BASE = "frontend/templates/base.html"
GDEC = "scripts/gate_decision.py"
GREP = "scripts/gate_report.py"
GSQL = "scripts/gate_metrics.sql"

EXPORT = ["tests/test_export.py"]
DELETE = ["tests/test_account_deletion.py"]
PURGE = ["tests/test_purge.py"]
PRIVT = ["tests/test_privacy_and_settings.py", "tests/test_release_checklist.py"]
CARD = ["tests/test_no_card_numbers.py"]
GDT = ["tests/test_gate_decision.py"]
GCLI = ["tests/test_gate_report.py"]
PGP = ["tests/test_account_and_purge_pg.py"]
PGG = ["tests/test_gate_sql_pg.py"]

# (file, old, new, label, tests, needs_pg)
M = []
def add(f, old, new, label, tests, pg=False):
    M.append((f, old, new, label, tests, pg))

# --- export: which rows --------------------------------------------------------------------------
add(SVC, "        conditions = [Expense.user_id == user_id]\n", "        conditions = []\n", "export: not scoped to the user", EXPORT)
add(SVC, "Expense.id > last[1]", "Expense.id >= last[1]", "export: a batch repeats its last row", EXPORT)
add(SVC, "            .where(*conditions)\n            .order_by(Expense.occurred_on, Expense.id).limit(batch_size)", "            .where(*conditions, Expense.deleted_at.is_(None))\n            .order_by(Expense.occurred_on, Expense.id).limit(batch_size)", "export: drops soft-deleted rows", EXPORT)
add(SVC, "deleted_at=None if e.deleted_at is None else _aware(e.deleted_at),", "deleted_at=None,", "export: hides deleted_at", EXPORT)
add(SVC, "subcategory_slug=sub_slug, subcategory_name_en=sub_name, note=e.note,", "subcategory_slug=sub_slug, subcategory_name_en=sub_name, note=None,", "export: leaves out notes", EXPORT)
# --- export: format ------------------------------------------------------------------------------
for ch, name in (("=", "="), ("+", "+"), ("-", "-"), ("@", "@")):
    add(FMT, '_FORMULA_START = ("=", "+", "-", "@", "\\t", "\\r", "\\n")', '_FORMULA_START = (' + ", ".join(f'"{c}"' for c in ("=", "+", "-", "@") if c != ch) + ', "\\t", "\\r", "\\n")', f"csv: '{name}' is not neutralised", EXPORT)
add(FMT, '"@", "\\t", "\\r", "\\n")', '"@", "\\r", "\\n")', "csv: tab is not neutralised", EXPORT)
add(FMT, '"@", "\\t", "\\r", "\\n")', '"@", "\\t", "\\n")', "csv: carriage return is not neutralised", EXPORT)
add(FMT, "    probe = value.lstrip(\" \\u00a0\")\n", "    probe = value\n", "csv: a formula after leading spaces is not neutralised", EXPORT)
add(FMT, 'BOM = "\\ufeff"', 'BOM = ""', "csv: no BOM (Excel garbles Urdu)", EXPORT)
add(FMT, 'return f"{paisa // 100}.{paisa % 100:02d}"', 'return f"{paisa // 100}.{paisa % 100}"', "money: 5 paisa written as .5", EXPORT)
add(FMT, 'cells.append("true" if value else "false")', 'cells.append("True" if value else "False")', "csv: booleans written differently from JSON", EXPORT)
add(FMT, 'writer = csv.writer(out, lineterminator="\\r\\n")', 'writer = csv.writer(out, lineterminator="\\n")', "csv: LF instead of CRLF", EXPORT)
# --- export: route -------------------------------------------------------------------------------
add(ROUTER, "    wait = export_limiter.check(user.id)\n", "    wait = 0\n", "export route: no rate limit", EXPORT)
add(ROUTER, '"Cache-Control": "no-store",', '"Cache-Control": "private",', "export route: may be cached", EXPORT)
add(ROUTER, "if fmt not in (\"json\", \"csv\"):", "if fmt not in (\"json\", \"csv\", \"xml\"):", "export route: accepts any format", EXPORT)
add(ROUTER, "attachment; filename=", "inline; filename=", "export route: shown in the browser, not downloaded", EXPORT)
add(ROUTER, "    svc.run_maintenance(db)          # at most once a day", "    pass                             # at most once a day", "list route: no longer starts the purge", PURGE)
# --- purge and maintenance -----------------------------------------------------------------------
add(SVC, "Expense.deleted_at < cutoff)", "Expense.deleted_at <= cutoff)", "purge: a row deleted exactly 30 days ago is purged", PURGE)
add(SVC, "PURGE_AFTER_DAYS = 30 ", "PURGE_AFTER_DAYS = 31 ", "purge: 31 days instead of 30", PURGE + PRIVT)
add(SVC, "delete(Expense).where(Expense.deleted_at.is_not(None), Expense.deleted_at < cutoff)", "delete(Expense).where(Expense.created_at < cutoff)", "purge: removes old LIVE rows", PURGE)
add(SVC, '_audit(db, None, "system", "expense.purge", None, {"count": removed})', '_audit(db, None, "system", "expense.purge", None, {"count": removed + 1})', "purge: wrong count in the audit row", PURGE)
add(SVC, "MAINTENANCE_INTERVAL = timedelta(hours=24)", "MAINTENANCE_INTERVAL = timedelta(hours=12)", "maintenance: runs twice a day", PURGE)
add(SVC, "_aware(last) > now - MAINTENANCE_INTERVAL", "_aware(last) >= now - MAINTENANCE_INTERVAL", "maintenance: waits one tick too long", PURGE)
add(SVC, "MaintenanceRun.last_run_at <= now - MAINTENANCE_INTERVAL", "MaintenanceRun.last_run_at < now - MAINTENANCE_INTERVAL", "maintenance: claim off by one", PURGE)
add(SVC, "    except Exception:                                    # housekeeping must never break a request", "    except KeyError:                                     # housekeeping must never break a request", "maintenance: can raise", PURGE)
add(SVC, "        db.rollback()\n        log.warning(\"expense maintenance failed", "        log.warning(\"expense maintenance failed", "maintenance: no rollback after a failure", PURGE)
add(SVC, "            claimed = db.execute(\n                _insert_run_ignore(db).values(name=MAINTENANCE_PURGE, last_run_at=now).returning(MaintenanceRun.name)\n            ).first() is not None",
    "            claimed = db.execute(\n                _insert_run_ignore(db).values(name=MAINTENANCE_PURGE, last_run_at=now)\n            ).rowcount != 0", "maintenance: claim decided by rowcount (not atomic)", PGP, pg=True)
# --- account deletion ----------------------------------------------------------------------------
add(AUTH, "    if not verify_password(body.password, current_user.hashed_password):", "    if False:", "delete: password is not checked", DELETE)
add(AUTH, "    if x_requested_with != REQUIRED_HEADER[1]:", "    if False:", "delete: anti-forgery header not required", DELETE)
add(AUTH, "    wait = delete_limiter.check(current_user.id)\n", "    wait = 0\n", "delete: no limit on password guesses", DELETE)
add(AUTH, "    _clear_session(response)\n    logger.info(\"account deleted", "    logger.info(\"account deleted", "delete: cookies not cleared", DELETE)
add(AUTH, "    if not account_service.delete_account(db, user_id):", "    if False:", "delete: the account is never deleted", DELETE)
add(AUTH, 'REQUIRED_HEADER = ("X-Requested-With", "ShariahEase")', 'REQUIRED_HEADER = ("X-Requested-With", "Shariah")', "delete: header value differs from the expense routes", DELETE)
add(MODELS, '    chat_messages    = relationship("ChatMessage",     back_populates="user", cascade="all, delete-orphan")', '    chat_messages    = relationship("ChatMessage",     back_populates="user", cascade="save-update")', "delete: chat history not removed with the account", DELETE)
add(MODELS, '    user_id     = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)\n    actor', '    user_id     = Column(Integer, ForeignKey("users.id"), nullable=True)\n    actor', "delete: audit_log loses its cascade (model)", DELETE)
# --- privacy page, settings, links ---------------------------------------------------------------
add(PRIV, "<strong>We never store card numbers, CVV codes or bank account numbers.</strong>", "<strong>We keep payment details safe.</strong>", "privacy: no longer says no card numbers", PRIVT)
add(PRIV, "it stays for 30 days so you can undo it", "it stays for 60 days so you can undo it", "privacy: wrong undo period", PRIVT)
add(PRIV, "for about 30 minutes", "for about 3 hours", "privacy: wrong guest-memory time", PRIVT)
add(PRIV, "Activity log</dt>", "Log</dt>", "privacy: activity log not mentioned", PRIVT)
add(PAGES, "[A-Za-z0-9._%+\\-]+@", ".+@", "privacy: contact address not validated", PRIVT)
add(SETJS, "const WRITE_HEADER = { 'X-Requested-With': 'ShariahEase' };", "const WRITE_HEADER = {};", "settings: anti-forgery header not sent", PRIVT)
add(SETJS, "body: JSON.stringify({ password: this.password })", "body: JSON.stringify({})", "settings: password not sent", PRIVT)
add(SETJS, "store.remove('se_token'); store.remove('se_user');", "", "settings: login token kept after deleting", PRIVT)
add(BASE, '<a href="/privacy"', '<a href="/privacyx"', "links: no Privacy link on any page", PRIVT)
add(BASE, '<a href="/settings"\n         class="flex items-center gap-3 px-3 py-2 rounded-lg transition-all duration-200', '<a href="/settingsx"\n         class="flex items-center gap-3 px-3 py-2 rounded-lg transition-all duration-200', "links: no Settings link in the sidebar", PRIVT)
# --- no card numbers -----------------------------------------------------------------------------
add("tests/test_no_card_numbers.py", '"payment_method", "source", "shariah_flag"', '"source", "shariah_flag"', "card guard: an unreviewed column goes unnoticed (self-check)", CARD)
add("tests/test_no_card_numbers.py", "r\"(card|cvv|cvc|cvn|\\bpan\\b|iban|", "r\"(cvv|cvc|cvn|\\bpan\\b|iban|", "card guard: 'card' dropped from the pattern (self-check)", CARD)
# --- gate decision -------------------------------------------------------------------------------
add(GDEC, "RETENTION_MIN_SAMPLE, RETENTION_GREEN, RETENTION_AMBER = 30, 30, 15", "RETENTION_MIN_SAMPLE, RETENTION_GREEN, RETENTION_AMBER = 30, 31, 15", "gate: retention green at 31%", GDT)
add(GDEC, "RETENTION_MIN_SAMPLE, RETENTION_GREEN, RETENTION_AMBER = 30, 30, 15", "RETENTION_MIN_SAMPLE, RETENTION_GREEN, RETENTION_AMBER = 30, 30, 16", "gate: retention amber at 16%", GDT)
add(GDEC, "RETENTION_MIN_SAMPLE, RETENTION_GREEN, RETENTION_AMBER = 30, 30, 15", "RETENTION_MIN_SAMPLE, RETENTION_GREEN, RETENTION_AMBER = 31, 30, 15", "gate: retention minimum sample 31", GDT)
add(GDEC, "ADOPTION_MIN_SAMPLE, ADOPTION_GREEN, ADOPTION_AMBER = 20, 25, 10", "ADOPTION_MIN_SAMPLE, ADOPTION_GREEN, ADOPTION_AMBER = 20, 26, 10", "gate: adoption green at 26%", GDT)
add(GDEC, "ADOPTION_MIN_SAMPLE, ADOPTION_GREEN, ADOPTION_AMBER = 20, 25, 10", "ADOPTION_MIN_SAMPLE, ADOPTION_GREEN, ADOPTION_AMBER = 20, 25, 11", "gate: adoption amber at 11%", GDT)
add(GDEC, "ADOPTION_MIN_SAMPLE, ADOPTION_GREEN, ADOPTION_AMBER = 20, 25, 10", "ADOPTION_MIN_SAMPLE, ADOPTION_GREEN, ADOPTION_AMBER = 21, 25, 10", "gate: adoption minimum sample 21", GDT)
add(GDEC, "CORRECTION_MIN_SAMPLE, CORRECTION_GREEN, CORRECTION_AMBER = 30, 25, 40", "CORRECTION_MIN_SAMPLE, CORRECTION_GREEN, CORRECTION_AMBER = 30, 26, 40", "gate: correction green up to 26%", GDT)
add(GDEC, "CORRECTION_MIN_SAMPLE, CORRECTION_GREEN, CORRECTION_AMBER = 30, 25, 40", "CORRECTION_MIN_SAMPLE, CORRECTION_GREEN, CORRECTION_AMBER = 30, 25, 41", "gate: correction amber up to 41%", GDT)
add(GDEC, "CORRECTION_MIN_SAMPLE, CORRECTION_GREEN, CORRECTION_AMBER = 30, 25, 40", "CORRECTION_MIN_SAMPLE, CORRECTION_GREEN, CORRECTION_AMBER = 31, 25, 40", "gate: correction minimum sample 31", GDT)
add(GDEC, "UPGRADE_GREEN_USERS = 10", "UPGRADE_GREEN_USERS = 11", "gate: upgrade interest green at 11 users", GDT)
add(GDEC, "C1_MIN_STARTERS = 30", "C1_MIN_STARTERS = 31", "C1: 31 starters needed", GDT)
add(GDEC, "C1_MIN_ACTIVATED_PERCENT = 30", "C1_MIN_ACTIVATED_PERCENT = 31", "C1: 31% activated needed", GDT)
add(GDEC, "    elif num * 100 >= green * den:", "    elif num * 100 > green * den:", "gate: green needs strictly more than the threshold", GDT)
add(GDEC, "    elif num * 100 >= amber * den:", "    elif num * 100 > amber * den:", "gate: amber needs strictly more than the threshold", GDT)
add(GDEC, "    if den < min_sample:\n        out[\"status\"]", "    if den <= min_sample:\n        out[\"status\"]", "gate: minimum sample is exclusive", GDT)
add(GDEC, "    elif num * 100 <= CORRECTION_GREEN * den:", "    elif num * 100 < CORRECTION_GREEN * den:", "gate: correction green is exclusive", GDT)
add(GDEC, "    elif num * 100 <= CORRECTION_AMBER * den:", "    elif num * 100 < CORRECTION_AMBER * den:", "gate: correction amber is exclusive", GDT)
add(GDEC, "    if den < CORRECTION_MIN_SAMPLE:\n        correction", "    if den <= CORRECTION_MIN_SAMPLE:\n        correction", "gate: correction minimum sample is exclusive", GDT)
add(GDEC, "        activated_ok = act * 100 >= C1_MIN_ACTIVATED_PERCENT * judge", "        activated_ok = act * 100 > C1_MIN_ACTIVATED_PERCENT * judge", "C1: exactly 30% is not enough", GDT)
add(GDEC, "    enough_starters = starters >= C1_MIN_STARTERS", "    enough_starters = starters > C1_MIN_STARTERS", "C1: exactly 30 starters is not enough", GDT)
add(GDEC, '"status": "green" if users >= UPGRADE_GREEN_USERS else "amber"}', '"status": "green" if users >= UPGRADE_GREEN_USERS else "red"}', "gate: upgrade interest can be red", GDT)
add(GDEC, 'verdict = "red" if "red" in statuses else "amber" if "amber" in statuses else "green"', 'verdict = "amber" if "amber" in statuses else "red" if "red" in statuses else "green"', "gate: amber beats red", GDT)
add(GDEC, '    if retention["status"] == "inconclusive" or adoption["status"] == "inconclusive":\n        verdict', '    if retention["status"] == "inconclusive" and adoption["status"] == "inconclusive":\n        verdict', "gate: inconclusive only when both samples are short", GDT)
add(GDEC, "            raise ValueError(f\"inconsistent counts: {small}", "            pass  # f\"inconsistent counts: {small}", "gate: inconsistent counts are accepted", GDT)
# --- gate report ---------------------------------------------------------------------------------
add(GREP, "    cohort_end = as_of - 28 * DAY if mode == \"gate\" else as_of", "    cohort_end = as_of - 27 * DAY if mode == \"gate\" else as_of", "report: cohort ends 27 days before as-of", GCLI)
add(GREP, "    if as_of <= cohort_start:", "    if as_of < cohort_start:", "report: as-of equal to cohort start is accepted", GCLI)
add(GREP, "    if value.tzinfo is None:", "    if False:", "report: a date without a zone is guessed", GCLI)
add(GREP, "    while path.exists():", "    while False:", "report: an earlier run is overwritten", GCLI)
add(GREP, 'load_dotenv(Path.cwd() / ".env")', "load_dotenv()", "report: searches for a .env up the tree (can reach a real database)", GCLI)
add(GREP, "    if not url.startswith(\"postgresql\"):", "    if False:", "report: runs against SQLite", GCLI)
add(GREP, "({type(e).__name__}). Check DATABASE_URL", "({e}). Check DATABASE_URL", "report: a connection error prints the database URL", GCLI)
add(GREP, 'conn.exec_driver_sql("SET TRANSACTION READ ONLY")', "pass", "report: not read-only", GCLI + PGG, pg=True)
add(GREP, "conn.exec_driver_sql(\"SET LOCAL TIME ZONE 'UTC'\")", "pass", "report: session time zone is not UTC", GCLI + PGG, pg=True)
# --- gate SQL (PG) -------------------------------------------------------------------------------
def sql(old, new, label):
    add(GSQL, old, new, "gate sql: " + label, PGG, pg=True)
sql("  WHERE NOT is_admin\n", "  WHERE TRUE\n", "admins are counted")
sql("WHERE f.first_at >= p.cohort_start AND f.first_at < p.cohort_end", "WHERE f.first_at >= p.cohort_start AND f.first_at <= p.cohort_end", "cohort end is inclusive")
sql("WHERE f.first_at >= p.cohort_start AND f.first_at < p.cohort_end", "WHERE f.first_at > p.cohort_start AND f.first_at < p.cohort_end", "cohort start is exclusive")
sql("WHERE s.first_at <= p.as_of - interval '7 days'", "WHERE s.first_at < p.as_of - interval '7 days'", "judgeable at exactly 7 days is excluded")
sql("AND e.created_at <  j.first_at + interval '7 days'", "AND e.created_at <  j.first_at + interval '6 days'", "activation window is 6 days")
sql("HAVING COUNT(*) >= 3", "HAVING COUNT(*) >= 2", "activation needs only 2 entries")
sql("COUNT(DISTINCT e.local_day) >= 2", "COUNT(DISTINCT e.local_day) >= 1", "activation needs only 1 day")
sql("CAST((x.created_at AT TIME ZONE u.tz) AS date)", "CAST((x.created_at AT TIME ZONE 'UTC') AS date)", "local day taken in UTC")
sql("WHERE a.first_at <= p.as_of - interval '28 days'", "WHERE a.first_at <= p.as_of - interval '27 days'", "retention base is 27 days old")
sql("AND e.created_at >= a.first_at + interval '21 days'", "AND e.created_at >= a.first_at + interval '20 days'", "retention window starts at day 20")
sql("AND e.created_at <  a.first_at + interval '28 days') AS retained", "AND e.created_at <= a.first_at + interval '28 days') AS retained", "retention window includes day 28")
sql("WHERE e.created_at >= p.as_of - interval '14 days' AND e.created_at < p.as_of", "WHERE e.created_at >= p.as_of - interval '13 days' AND e.created_at < p.as_of", "adoption window is 13 days")
sql("BOOL_OR(e.source IN ('ai_chat', 'whatsapp'))", "BOOL_OR(e.source IN ('ai_chat'))", "whatsapp is not an AI source (adoption)")
sql("WHERE e.source IN ('ai_chat', 'whatsapp')\n    AND e.created_at >  p.as_of - interval '30 days'", "WHERE e.source IN ('ai_chat')\n    AND e.created_at >  p.as_of - interval '30 days'", "whatsapp is not an AI source (correction)")
sql("AND e.created_at >  p.as_of - interval '30 days'", "AND e.created_at >= p.as_of - interval '30 days'", "correction window includes exactly 30 days")
sql("AND e.created_at <= p.as_of - interval '24 hours'", "AND e.created_at <  p.as_of - interval '24 hours'", "correction needs more than 24 hours")
sql("AND l.action IN ('expense.update', 'expense.delete')", "AND l.action IN ('expense.update')", "a delete is not a correction")
sql("AND l.user_id     = a.user_id\n", "", "a correction by another user counts")
sql("AND l.created_at <  a.created_at + interval '24 hours'\n", "AND l.created_at <= a.created_at + interval '24 hours'\n", "correction window includes 24 hours")
sql("WHERE l.entity_type = 'expense'\n                  AND l.entity_id   = CAST(a.id AS text)", "WHERE l.entity_id   = CAST(a.id AS text)", "a non-expense audit row counts")
sql("WHERE l.action = 'upgrade_interest' AND l.created_at < p.as_of", "WHERE l.action = 'upgrade_interest' AND l.created_at <= p.as_of", "interest at exactly as-of counts")
sql("  JOIN u ON u.id = l.user_id\n  CROSS JOIN p\n  WHERE l.action = 'upgrade_interest'", "  LEFT JOIN u ON u.id = l.user_id\n  CROSS JOIN p\n  WHERE l.action = 'upgrade_interest'", "admins' interest counts")
sql("  WHERE x.created_at < p.as_of\n", "  WHERE x.deleted_at IS NULL AND x.created_at < p.as_of\n", "soft-deleted expenses are ignored")
sql("ELSE 'Asia/Karachi' END", "ELSE 'UTC' END", "a bad time zone falls back to UTC")
sql("PERCENTILE_DISC(0.5)", "PERCENTILE_DISC(0.6)", "median is not the lower median")
sql("CAST(FLOOR(EXTRACT(EPOCH FROM (s.first_at - u.created_at)) / 60) AS bigint)", "CAST(ROUND(EXTRACT(EPOCH FROM (s.first_at - u.created_at)) / 60) AS bigint)", "minutes are rounded, not floored")


def run_tests(repo: Path, tests, pg: bool) -> bool:
    cmd = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *(["-m", "pg"] if pg else []), *tests]
    try:
        return subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=120 if pg else 40).returncode == 0
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
    bad = [p for rel, old, new, label, _, _ in M for _t, p in [_apply((ROOT / rel).read_text(encoding="utf-8"), old, new, label)] if p]
    if bad:
        print("\n".join(bad))
        return 2
    if check_only:
        print(f"all {len(M)} mutations apply exactly once ({sum(1 for m in M if m[5])} need PostgreSQL)")
        return 0
    have_pg = bool(os.getenv("DATABASE_URL_TEST")) and "--no-pg" not in sys.argv
    survived, skipped, ran = [], [], 0
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "base"
        shutil.copytree(ROOT, base, ignore=IGNORE)
        (base / "knowledge_base").mkdir(exist_ok=True)
        groups = {(tuple(m[4]), m[5]) for m in chosen}
        for tests, pg in sorted(groups):
            if pg and not have_pg:
                continue
            if not run_tests(base, list(tests), pg):
                print(f"The unmodified tests already fail: {tests}")
                return 2
        for rel, old, new, label, tests, pg in chosen:
            if pg and not have_pg:
                skipped.append(label)
                print("SKIPPED  (needs DATABASE_URL_TEST) " + label, flush=True)
                continue
            work = Path(tmp) / "work"
            if work.exists():
                shutil.rmtree(work)
            shutil.copytree(base, work)
            text, _ = _apply((work / rel).read_text(encoding="utf-8"), old, new, label)
            (work / rel).write_text(text, encoding="utf-8")
            caught = not run_tests(work, tests, pg)
            ran += 1
            print(("CAUGHT   " if caught else "SURVIVED ") + label, flush=True)
            if not caught:
                survived.append(label)
    print(f"\n{ran - len(survived)}/{ran} mutations caught" + (f", {len(skipped)} skipped (need PostgreSQL)" if skipped else ""))
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
