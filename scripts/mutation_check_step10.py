"""
Mutation check for step 10 (REST API, money parsing, write limiter, page wiring and service worker): login on every
route, ownership (user id from the session), 404/409/422/429 mapping, the anti-forgery header, month windows,
money parsing, the limiter, and what the service worker may touch.

For each mutation: copy the repo to a temp folder, break ONE thing in the copy, run the step 10 tests, and require
them to FAIL. A mutation that survives means a test is missing.

    python scripts/mutation_check_step10.py            # all mutations (about 15 s per mutation)
    python scripts/mutation_check_step10.py --check    # only verify that every mutation applies exactly once
    python scripts/mutation_check_step10.py --slice 0:20
    python scripts/mutation_check_step10.py --only sw:  # only mutations whose label contains this text
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "*.db", "venv", ".venv", ".pytest_cache", "knowledge_base")
R = "app/routers/expenses.py"
MONEY = "app/services/money.py"
LIM = "app/services/rate_limit.py"
SW = "frontend/static/sw.js"

# an "anonymous user" that is always user 1: what a route without the login dependency would effectively do
ANON = 'Depends(lambda: __import__("types").SimpleNamespace(id=1))'

# (file, old, new, label)
MUTATIONS = [
    # --- login required --------------------------------------------------------------------------
    (R, "def current_user(user: models.User = Depends(get_current_user)) -> models.User:", f"def current_user(user: models.User = {ANON}) -> models.User:", "login: current_user does not check the token"),
    (R, "def categories(user: models.User = Depends(current_user), db: Session = Depends(get_db)):", f"def categories(user: models.User = {ANON}, db: Session = Depends(get_db)):", "categories: no login"),
    (R, "    user: models.User = Depends(current_user),\n    db: Session = Depends(get_db),\n):\n    start, end = _window(month, date_from, date_to)\n    if start is None", f"    user: models.User = {ANON},\n    db: Session = Depends(get_db),\n):\n    start, end = _window(month, date_from, date_to)\n    if start is None", "summary: no login"),
    (R, "    user: models.User = Depends(current_user),\n    db: Session = Depends(get_db),\n):\n    start, end = _window(month, date_from, date_to)\n    if (before_date", f"    user: models.User = {ANON},\n    db: Session = Depends(get_db),\n):\n    start, end = _window(month, date_from, date_to)\n    if (before_date", "list: no login"),
    (R, "def create_expense(body: ExpenseCreate, user: models.User = Depends(write_user)", f"def create_expense(body: ExpenseCreate, user: models.User = {ANON}", "create: no login, header or limit"),
    (R, "def update_expense(expense_id: int, body: ExpensePatch, user: models.User = Depends(write_user),", f"def update_expense(expense_id: int, body: ExpensePatch, user: models.User = {ANON},", "update: no login, header or limit"),
    (R, "def delete_expense(expense_id: int, user: models.User = Depends(write_user)", f"def delete_expense(expense_id: int, user: models.User = {ANON}", "delete: no login, header or limit"),
    (R, "def restore_expense(expense_id: int, user: models.User = Depends(write_user)", f"def restore_expense(expense_id: int, user: models.User = {ANON}", "restore: no login, header or limit"),
    (R, "    user: models.User = Depends(current_user),\n    x_requested_with", f"    user: models.User = {ANON},\n    x_requested_with", "write_user: login not required"),
    # --- ownership: the user id always comes from the session ------------------------------------
    (R, "svc.add_expenses(db, user.id, [item]", "svc.add_expenses(db, 1, [item]", "create: written for user 1, not the session user"),
    (R, "svc.update_expense(db, user.id,", "svc.update_expense(db, 1,", "update: not scoped to the session user"),
    (R, "svc.delete_expenses(db, user.id,", "svc.delete_expenses(db, 1,", "delete: not scoped to the session user"),
    (R, "svc.restore_expenses(db, user.id,", "svc.restore_expenses(db, 1,", "restore: not scoped to the session user"),
    (R, "svc.get_expenses(db, user.id, expense_ids=[expense_id])", "svc.get_expenses(db, 1, expense_ids=[expense_id])", "restore: answer read for user 1"),
    (R, "svc.get_expenses(db, user.id, date_from=start", "svc.get_expenses(db, 1, date_from=start", "list: not scoped to the session user"),
    (R, "svc.summarize(db, user.id,", "svc.summarize(db, 1,", "summary: not scoped to the session user"),
    # --- status codes ----------------------------------------------------------------------------
    (R, 'return HTTPException(status_code=404, detail={"code": exc.code})', 'return HTTPException(status_code=403, detail={"code": exc.code})', "another user's row answers 403, not 404"),
    (R, 'return HTTPException(status_code=409, detail={"code": exc.code, "current_version": exc.current_version})', 'return HTTPException(status_code=400, detail={"code": exc.code, "current_version": exc.current_version})', "stale edit is not 409"),
    (R, 'return HTTPException(status_code=409, detail={"code": exc.code, "current_version": exc.current_version})', 'return HTTPException(status_code=409, detail={"code": exc.code})', "409 hides the current version"),
    (R, 'return HTTPException(status_code=422, detail={"code": exc.code, "errors": exc.errors})', 'return HTTPException(status_code=400, detail={"code": exc.code, "errors": exc.errors})', "invalid input is not 422"),
    (R, 'return HTTPException(status_code=422, detail={"code": "invalid_input",', 'return HTTPException(status_code=400, detail={"code": "invalid_input",', "bad amount is not 422"),
    (R, '    if not res.changed:\n        raise _fail(svc.NotFound("expense not found"))\n    return res.model_dump()', "    return res.model_dump()", "delete: a missing or foreign id answers 200"),
    (R, '    if not res.changed:\n        raise _fail(svc.NotFound("expense not found"))\n    page = ', "    page = ", "restore: a missing or foreign id answers 200"),
    (R, "    if result.already_deleted:", "    if False:", "create: retry of a deleted expense is not refused"),
    (R, "status_code=201 if result.created else 200", "status_code=201", "create: a retry is not told apart (always 201)"),
    (R, '"created" if result.created else "duplicate"', '"created"', "create: status label"),
    (R, 'source="manual")[0]', 'source="ai_chat")[0]', "create: wrong source"),
    # --- anti-forgery header and write limit -----------------------------------------------------
    (R, "    if x_requested_with != REQUIRED_HEADER_VALUE:", "    if False:", "writes do not need the X-Requested-With header"),
    (R, "    if wait > 0:", "    if False:", "write limit never blocks"),
    (R, "wait = write_limiter.check(user.id)", 'wait = write_limiter.check("everyone")', "write limit shared by all users"),
    (R, ',\n                            headers={"Retry-After": str(int(wait) + 1)})', ")", "429 without Retry-After"),
    (R, "WRITES_PER_MINUTE = 60", "WRITES_PER_MINUTE = 6000", "write limit far too high"),
    # --- request bodies and queries --------------------------------------------------------------
    (R, 'class ExpenseCreate(BaseModel):\n    model_config = ConfigDict(extra="forbid")', 'class ExpenseCreate(BaseModel):\n    model_config = ConfigDict(extra="ignore")', "create body accepts extra fields"),
    (R, 'class ExpensePatch(BaseModel):\n    model_config = ConfigDict(extra="forbid")', 'class ExpensePatch(BaseModel):\n    model_config = ConfigDict(extra="ignore")', "update body accepts extra fields"),
    (R, "    version: StrictInt  ", "    version: int  ", "update: version accepts text"),
    (R, "(0[1-9]|1[0-2])$", "(0[1-9]|1[0-3])$", "month 13 accepted"),
    (R, "        if not 2000 <= year <= 2100:", "        if False:", "month year range not checked"),
    (R, "date(year + 1, 1, 1) if mon == 12", "date(year, 1, 1) if mon == 12", "December window ends in the wrong year"),
    (R, "date(year, mon + 1, 1)", "date(year, mon + 1, 2)", "month window ends a day late"),
    (R, "        if date_from is not None or date_to is not None:", "        if False:", "month and date_from can be mixed"),
    (R, "    if (before_date is None) != (before_id is None):", "    if False:", "half a paging cursor accepted"),
    (R, '{"date": nxt[0], "id": nxt[1]}', '{"date": nxt[0], "id": nxt[0]}', "paging cursor id wrong"),
    (R, "    given = body.model_fields_set - {\"version\"}", "    given = {k for k in body.model_fields_set if getattr(body, k) is not None} - {\"version\"}", "update: note cannot be cleared"),
    # --- money -----------------------------------------------------------------------------------
    (MONEY, "    if len(frac) > 2:", "    if len(frac) > 3:", "money: three decimals accepted"),
    (MONEY, "    if paisa <= 0:", "    if paisa < 0:", "money: zero accepted"),
    (MONEY, "    if paisa > MAX_AMOUNT_PAISA:", "    if paisa >= MAX_AMOUNT_PAISA:", "money: the cap itself refused"),
    (MONEY, "    if paisa > MAX_AMOUNT_PAISA:", "    if False:", "money: no cap"),
    (MONEY, ".translate(_ARABIC_INDIC).translate(_EXT_ARABIC_INDIC)", ".translate(_EXT_ARABIC_INDIC)", "money: Arabic digits not read"),
    (MONEY, ".translate(_SEPARATORS)", "", "money: Arabic separators not read"),
    (MONEY, "    if isinstance(value, bool):\n        raise MoneyError(\"format\")", "    if False:\n        raise MoneyError(\"format\")", "money: booleans accepted"),
    (MONEY, 're.ASCII)', '0)', "money: other scripts' digits accepted"),
    (MONEY, r"(?:,\d{2,3})+", r"(?:,\d+)+", "money: any comma grouping accepted"),
    (MONEY, 'frac.ljust(2, "0")', 'frac.rjust(2, "0")', "money: 1.5 read as 1.05"),
    (MONEY, "paisa = int(whole) * 100", "paisa = int(whole) * 10", "money: rupees to paisa factor"),
    # --- limiter ---------------------------------------------------------------------------------
    (LIM, "while hits and now - hits[0] >= self.window:", "while hits and now - hits[0] > self.window:", "limiter: window edge"),
    (LIM, "            if len(hits) >= self.limit:", "            if len(hits) > self.limit:", "limiter: one hit too many allowed"),
    (LIM, "            hits.append(now)\n            if len", "            pass\n            if len", "limiter: hits not recorded"),
    (LIM, "            if len(hits) >= self.limit:\n                return", "            if len(hits) >= self.limit:\n                hits.append(now)\n                return", "limiter: blocked attempts extend the block"),
    (LIM, "            if len(self._hits) > self.max_keys:", "            if False:", "limiter: memory not bounded"),
    (LIM, "return max(self.window - (now - hits[0]), 0.001)", "return self.window", "limiter: wrong wait time"),
    # --- page wiring, manifest, service worker ---------------------------------------------------
    ("main.py", "app.include_router(expenses.router)\n", "", "main: expenses router not registered"),
    ("app/routers/pages.py", 'headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})', 'headers={"Cache-Control": "no-cache"})', "sw.js served without Service-Worker-Allowed"),
    ("app/routers/pages.py", 'headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})', 'headers={"Service-Worker-Allowed": "/"})', "sw.js may be cached"),
    ("frontend/templates/base.html", '{% block chart_js %}<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>{% endblock %}', "{% block chart_js %}{% endblock %}", "other pages lose their chart script"),
    ("frontend/templates/pages/expenses.html", '{% block chart_js %}<script src="/static/vendor/chartjs/chart.umd.js"></script>{% endblock %}', '{% block chart_js %}<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>{% endblock %}', "expenses page uses the unpinned CDN chart"),
    ("frontend/static/manifest.webmanifest", '"start_url": "/expenses"', '"start_url": "/"', "manifest start_url"),
    ("frontend/static/manifest.webmanifest", '"display": "standalone"', '"display": "browser"', "manifest display"),
    (SW, "          if (res.ok) cache.put(req, res.clone());", "          cache.put(req, res.clone());", "sw: failed answers are cached"),
    (SW, "if (req.method !== 'GET') return;", "if (false) return;", "sw: handles writes"),
    (SW, "if (url.origin !== self.location.origin) return;", "if (false) return;", "sw: handles other sites"),
    (SW, "if (req.headers.has('authorization')) return;", "if (false) return;", "sw: handles requests with a login header"),
    (SW, ".catch(() => caches.match(OFFLINE_URL))", ".catch(() => undefined)", "sw: no offline page"),
    (SW, "if (url.pathname.startsWith('/static/')) {", "if (true) {", "sw: handles every page"),
    ("frontend/static/js/expenses.js", "const WRITE_HEADER = { 'X-Requested-With': 'ShariahEase' };", "const WRITE_HEADER = {};", "page: writes without the header"),
]


def run_tests(repo: Path) -> bool:
    cmd = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider",
           "tests/test_money.py", "tests/test_expense_api.py", "tests/test_expense_pwa.py"]
    try:
        return subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=240).returncode == 0
    except subprocess.TimeoutExpired:          # a hang is a failure too: the broken code is noticed
        return False


def _apply(text: str, old: str, new: str, label: str):
    if text.count(old) != 1:
        return None, f"BAD MUTATION (found {text.count(old)}x): {label}"
    return text.replace(old, new), None


def main() -> int:
    check_only = "--check" in sys.argv
    lo, hi = 0, len(MUTATIONS)
    if "--slice" in sys.argv:
        a, b = sys.argv[sys.argv.index("--slice") + 1].split(":")
        lo, hi = int(a or 0), int(b or len(MUTATIONS))
    chosen = MUTATIONS[lo:hi]
    if "--only" in sys.argv:
        needle = sys.argv[sys.argv.index("--only") + 1]
        chosen = [m for m in chosen if needle in m[3]]
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
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "base"
        shutil.copytree(ROOT, base, ignore=IGNORE)
        (base / "knowledge_base").mkdir(exist_ok=True)
        if not run_tests(base):
            print("The unmodified tests already fail; fix that first.")
            return 2
        for rel, old, new, label in chosen:
            work = Path(tmp) / "work"
            if work.exists():
                shutil.rmtree(work)
            shutil.copytree(base, work)
            text, _ = _apply((work / rel).read_text(encoding="utf-8"), old, new, label)
            (work / rel).write_text(text, encoding="utf-8")
            caught = not run_tests(work)
            ran += 1
            print(("CAUGHT   " if caught else "SURVIVED ") + label, flush=True)
            if not caught:
                survived.append(label)
    print(f"\n{ran - len(survived)}/{ran} mutations caught")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
