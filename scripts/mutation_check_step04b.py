"""
Mutation check for step 4b (the AI expense backend): the atomic allowance, single-use cards, ownership (user_id from the
session only), expiry, router-before-guard order, the amount cross-check, all-or-nothing, the kill switch and breaker,
"no tracing / no logs / no voice / no history" for expense text, the strict form, and the 0005 migration.

For each mutation: copy the repo to a temp folder, break ONE thing in the copy, run the step 4b tests, and require them to
FAIL. A mutation that survives means a test is missing. Mutations labelled "pg:" need a real PostgreSQL (the parallel proof
that read-then-write would let 60 messages through a limit of 30): they run only when DATABASE_URL_TEST is set.

    python scripts/mutation_check_step04b.py            # all mutations
    python scripts/mutation_check_step04b.py --check    # only verify that every mutation applies exactly once
    python scripts/mutation_check_step04b.py --slice 0:20
    python scripts/mutation_check_step04b.py --only quota
"""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "*.db", "venv", ".venv", ".pytest_cache", "knowledge_base", "results",
                                "node_modules", "UI_components", "_step*", "backups")
TESTS = ["tests/test_expense_normalise.py", "tests/test_ai_limits.py", "tests/test_expense_understand.py", "tests/test_pending_actions.py",
         "tests/test_expense_ai_rules.py", "tests/test_expense_ai_flow.py", "tests/test_migrations.py", "tests/test_account_deletion.py",
         "tests/test_privacy_and_settings.py"]
PG_TESTS = ["tests/test_ai_quota_pg.py"]
Q, P, A, U, N = ("app/services/ai_limits.py", "app/services/pending_actions.py", "app/services/expense_ai.py",
                 "app/services/expense_understand.py", "app/services/expense_normalise.py")
R, C, M, MO = ("app/routers/expense_ai.py", "app/routers/chatbot.py", "alembic/versions/0005_ai_expense.py", "app/database/models.py")

CLAIM_C = 'PendingAction.id == row.id, PendingAction.user_id == user_id, PendingAction.status == "pending",\n                   PendingAction.expires_at > now)\n            .values(status="confirmed"'
CLAIM_X = 'PendingAction.id == row.id, PendingAction.user_id == user_id, PendingAction.status == "pending",\n                                PendingAction.expires_at > now)\n                         .values(status="cancelled"'
CHOOSE = 'PendingAction.id == row.id, PendingAction.user_id == user_id, PendingAction.status == "pending",\n                                    PendingAction.expires_at > now, PendingAction.rev == row.rev)'
UNDO = '.where(PendingAction.id == row.id, PendingAction.user_id == user_id, PendingAction.status == "confirmed",\n                   PendingAction.undone_at.is_(None), PendingAction.resolved_at >= now - UNDO_WINDOW)'

# (file, old, new, label)
MUTATIONS = [
    # --- quota: the atomic allowance ---------------------------------------------------------------
    (Q, "where=table.c.used < limit)", "where=table.c.used <= limit)", "quota: one message too many is allowed"),
    (Q, ', set_={"used": table.c.used + 1}, where=table.c.used < limit)', ', set_={"used": table.c.used + 1})', "quota: no limit in the statement"),
    (Q, "    if limit <= 0:\n        return None", "    if False:\n        return None", "quota: a limit of zero still lets the first message in"),
    (Q, '        .on_conflict_do_update(index_elements=["user_id", "period"], set_={"used": table.c.used + 1}, where=table.c.used < limit)\n        .returning(table.c.used)\n    )\n    try:\n        used = db.execute(statement).scalar()',
        '        .on_conflict_do_update(index_elements=["user_id", "period"], set_={"used": table.c.used + 1})\n        .returning(table.c.used)\n    )\n    try:\n        used = None if used_this_period(db, user_id, period) >= limit else db.execute(statement).scalar()',
        "pg: quota: read-then-write instead of one atomic statement"),
    (Q, "UsageCounter.period == period,\n                                              UsageCounter.used > 0)", "UsageCounter.period == period)", "quota: a release can go below zero"),
    (Q, "UsageCounter.user_id == user_id, UsageCounter.period == period,\n                                              UsageCounter.used > 0)", "UsageCounter.period == period,\n                                              UsageCounter.used > 0)", "quota: a release touches everybody's counter"),
    (Q, '    return now.astimezone(zone).strftime("%Y-%m")', '    return now.strftime("%Y-%m")', "quota: the month ignores the person's time zone"),
    (Q, "DEFAULT_FREE_LIMIT = 30", "DEFAULT_FREE_LIMIT = 31", "quota: the default is not 30"),
    (Q, "    return os.getenv(\"EXPENSE_AI_ENABLED\", \"true\").strip().lower() not in _FALSE", "    return True", "kill switch: ignored"),
    (Q, "return tokens_today(db, now) >= daily_token_cap()", "return tokens_today(db, now) > daily_token_cap()", "breaker: opens one token late"),
    (Q, "    tokens = max(0, int(tokens or 0))", "    tokens = int(tokens or 0)", "breaker: a negative cost gives tokens back"),
    (Q, '"tokens": table.c.tokens + tokens, "calls": table.c.calls + 1', '"tokens": table.c.tokens + 1, "calls": table.c.calls + 1', "breaker: counts calls, not tokens"),
    (Q, "    return (now or _utcnow()).astimezone(timezone.utc).date()", "    return (now or _utcnow()).date()", "breaker: the day is not the UTC day"),
    (Q, "DEFAULT_DAILY_TOKEN_CAP = 120_000", "DEFAULT_DAILY_TOKEN_CAP = 12_000_000", "breaker: the default cap is above the provider's"),
    # --- cards: single use, ownership, expiry --------------------------------------------------------
    (P, CLAIM_C, CLAIM_C.replace('PendingAction.status == "pending",\n                   ', "\n                   ", 1), "card: confirm does not require pending (second tap saves twice)"),
    (P, CLAIM_C, CLAIM_C.replace("PendingAction.expires_at > now", "True", 1), "card: confirm ignores expiry"),
    (P, CLAIM_C, CLAIM_C.replace("PendingAction.user_id == user_id, ", "", 1), "card: confirm does not check the owner in the claim"),
    (P, CLAIM_X, CLAIM_X.replace('PendingAction.status == "pending",\n                                ', "\n                                ", 1), "card: cancel does not require pending"),
    (P, CLAIM_X, CLAIM_X.replace("PendingAction.expires_at > now", "True", 1), "card: cancel ignores expiry"),
    (P, CLAIM_X, CLAIM_X.replace("PendingAction.user_id == user_id, ", "", 1), "card: cancel does not check the owner in the claim"),
    (P, 'PendingAction.id == card_id, PendingAction.user_id == user_id)).scalar_one_or_none()', 'PendingAction.id == card_id)).scalar_one_or_none()', "card: another person's card can be read by id"),
    (P, 'select(PendingAction).where(PendingAction.user_id == user_id,\n                                                  PendingAction.client_msg_id == client_msg_id)', 'select(PendingAction).where(PendingAction.client_msg_id == client_msg_id)', "card: a retry finds another person's card"),
    (P, 'PendingAction.status == "pending",\n                                                  PendingAction.expires_at > now)\n                      .order_by', 'PendingAction.status == "pending")\n                      .order_by', "card: a lapsed card still answers a plain yes"),
    (P, 'select(PendingAction).where(PendingAction.user_id == user_id, PendingAction.status == "pending",', 'select(PendingAction).where(PendingAction.status == "pending",', "card: a plain yes can pick another person's card"),
    (P, "    return uuid.uuid5(CARD_NAMESPACE, f\"{card_id}:{index}\")", "    return uuid.uuid4()", "card: a replayed confirm could add twice (random expense ids)"),
    (P, '.on_conflict_do_nothing(index_elements=["user_id", "client_msg_id"])', "", "card: the same message makes a second card (no conflict rule)"),
    (P, "PENDING_TTL = timedelta(minutes=15)", "PENDING_TTL = timedelta(minutes=30)", "card: the life is not 15 minutes"),
    (P, "UNDO_WINDOW = timedelta(hours=1)", "UNDO_WINDOW = timedelta(hours=3)", "card: the undo window is not an hour"),
    (P, "KEEP_DAYS = 7", "KEEP_DAYS = 70", "card: old cards are kept 70 days"),
    (P, "delete(PendingAction).where(PendingAction.user_id == user_id,", "delete(PendingAction).where(PendingAction.user_id != 0,", "card: the clean-up removes other people's old cards"),
    (P, 'return {"subcategory_slug": slug} if "." in slug else {"category_slug": slug}', 'return {"category_slug": slug}', "card: a specific category is saved as a group"),
    (P, '        db.rollback()               # the claim is rolled back too', '        db.commit()               # the claim is rolled back too', "card: a refused expense still burns the card"),
    (P, 'source="ai_chat", commit=False)', 'source="manual", commit=False)', "card: the saved expense is not marked as AI"),
    (P, "        if category_slug not in item.alternatives:\n            raise CardError(\"bad_choice\")", "        if False:\n            raise CardError(\"bad_choice\")", "choose: any category is accepted"),
    (P, "or not 0 <= item_index < len(payload.items)", "or False", "choose: any item number is accepted"),
    (P, CHOOSE, CHOOSE.replace("PendingAction.rev == row.rev", "True", 1), "choose: a parallel choose can overwrite another"),
    (P, CHOOSE, CHOOSE.replace('PendingAction.status == "pending",\n                                    ', "\n                                    ", 1), "choose: a used card can still be edited"),
    (P, CHOOSE, CHOOSE.replace("PendingAction.user_id == user_id, ", "", 1), "choose: the owner is not checked in the update"),
    (P, UNDO, UNDO.replace(", PendingAction.resolved_at >= now - UNDO_WINDOW", "", 1), "undo: no time limit"),
    (P, UNDO, UNDO.replace("PendingAction.undone_at.is_(None), ", "", 1), "undo: can be done twice"),
    (P, UNDO, UNDO.replace('PendingAction.status == "confirmed",', "True,", 1), "undo: works on a card that was never confirmed"),
    (P, UNDO, UNDO.replace("PendingAction.user_id == user_id, ", "", 1), "undo: the owner is not checked in the update"),
    (P, "    out = [slug] + same[:MAX_ALTERNATIVES - 2]", "    out = same[:MAX_ALTERNATIVES - 2]", "choose: the original category is not offered back"),
    (P, 'if "other.other" in {r[0] for r in rows} and "other.other" not in out:', "if False:", "choose: 'other' is never offered"),
    # --- the assistant: order of checks, cross-check, all or nothing ----------------------------------
    (A, "    if not ai_limits.ai_enabled():\n        return None", "    if False:\n        return None", "assistant: the kill switch is ignored"),
    (A, "    if not looks_like_expense_talk(text):\n        return None", "    if False:\n        return None", "assistant: every message is sent to the model"),
    (A, "    if ai_limits.breaker_open(db, now):", "    if False:", "assistant: the daily breaker is ignored"),
    (A, "    if ai_limits.try_consume(db, user_id, period) is None:", "    if False:", "assistant: the monthly allowance is ignored"),
    (A, "    if understood is None:\n        ai_limits.release(db, user_id, period)\n", "    if understood is None:\n", "assistant: a provider failure costs the allowance"),
    (A, "        ai_limits.release(db, user_id, period)\n        return Handled(_say(\"unavailable\", _pre_lang(text)), kind=\"info\")\n    if result.tokens:", "        return Handled(_say(\"unavailable\", _pre_lang(text)), kind=\"info\")\n    if result.tokens:", "assistant: a crash costs the allowance"),
    (A, "    if result.tokens:\n        ai_limits.add_tokens(db, result.tokens, now)", "    if False:\n        ai_limits.add_tokens(db, result.tokens, now)", "assistant: tokens are not counted for the breaker"),
    (A, '    if understood.intent in ("islamic_finance_qa", "off_topic", "unclear"):\n        ai_limits.release(db, user_id, period)', '    if understood.intent in ("islamic_finance_qa", "off_topic", "unclear"):\n        pass', "assistant: other topics cost the allowance"),
    (A, "    if payload is None:\n        ai_limits.release(db, user_id, period)", "    if payload is None:\n        pass", "assistant: a refused message costs the allowance"),
    (A, "    if not created:                                              # a parallel copy of this message won: it paid, we do not\n        ai_limits.release(db, user_id, period)", "    if not created:\n        pass", "assistant: a parallel duplicate is charged twice"),
    (A, "        if not amount_in_message(item.amount_text, text):", "        if False:", "assistant: the amount cross-check is skipped"),
    (A, "    if len(understood.items) > pa.MAX_ITEMS:", "    if False:", "assistant: any number of items"),
    (A, "max_future_days=es.MAX_FUTURE_DAYS", "max_future_days=3650", "assistant: dates far in the future are accepted"),
    (A, "if item.category_slug is None or item.category_slug not in valid:", "if item.category_slug is None:", "assistant: a category that does not exist is accepted"),
    (A, "- _AMBIGUOUS - {\"k\"}", "- {\"k\"}", "assistant: 'do' and 'ek' send chat to the model"),
    (A, '_YES = frozenset({"yes", "y",', '_YES = frozenset({"yes", "y", "ok", "ji",', "assistant: 'ok' confirms an expense"),
    (A, '    if answer == "no":\n            pa.cancel', '    if answer == "yes":\n            pa.cancel', "assistant: yes and no are swapped"),
    (A, "    if earlier is not None:", "    if False:", "assistant: a retry runs the model again"),
    (A, "{ur if payload.lang == 'ur' else en}", "{en}", "assistant: Urdu replies show English category names"),
    (A, '_MSG_ID = re.compile(r"^[A-Za-z0-9_\\-]{1,64}$")', '_MSG_ID = re.compile(r"^.+$")', "assistant: any text is trusted as a message id"),
    (A, "def yes_or_no(text: str) -> Optional[str]:\n    word = fold(text).strip(\" .!،۔؟?\")\n    if word in _YES:", "def yes_or_no(text: str) -> Optional[str]:\n    word = fold(text).strip(\" .!،۔؟?\")\n    if word in _YES or word.startswith(\"yes\"):", "assistant: 'yes and also ...' confirms"),
    # --- the model call ------------------------------------------------------------------------------
    (U, '"strict": True,', '"strict": False,', "call: strict mode is off"),
    (U, '    if "gpt-oss" in provider.model:', "    if True:", "call: reasoning effort sent to every model"),
    (U, '        body["reasoning_effort"] = REASONING_EFFORT', '        body["reasoning_effort"] = "high"', "call: not the lowest reasoning effort"),
    (U, '{"role": "system", "content": contract.prefix}, {"role": "user", "content": text}', '{"role": "system", "content": contract.prefix + text}, {"role": "user", "content": text}', "call: the person's words are inside the instructions"),
    (U, "return tracing_context(enabled=False)", "return tracing_context(enabled=True)", "call: LangSmith tracing is left on"),
    (U, "    return max(0, total - cached)", "    return max(0, total)", "call: cached tokens are charged"),
    (U, '        if base.lower().startswith("https://"):', "        if True:", "call: a plain-http fallback is used"),
    (U, "            if response.status_code != 200:", "            if False:", "call: an error status is read as an answer"),
    (U, '                problem = "bad_answer"\n                continue', '                problem = "bad_answer"\n                return UnderstandResult(None, tokens, None, problem)', "call: the fallback is skipped after a bad answer"),
    (U, '    model_config = ConfigDict(extra="forbid")\n\n\n@dataclass(frozen=True)', '    model_config = ConfigDict(extra="ignore")\n\n\n@dataclass(frozen=True)', "form: extra fields from the model are accepted"),
    (U, '            category_slug=None if i.category_slug.value == "none" else i.category_slug.value,', "            category_slug=i.category_slug.value,", "form: the 'none' category is treated as a real one"),
    (U, "        if ch in _BIDI:\n            continue", "        if False:\n            continue", "form: direction-override characters reach the model"),
    (U, "[:MAX_MESSAGE_CHARS]", "", "form: the message length is not limited"),
    (U, '        out["required"] = list(out["properties"].keys())', '        out["required"] = []', "form: not every field is required (strict mode needs it)"),

    (U, "            if not isinstance(body, dict):                       # a provider that answers with a list or a bare value\n                body = {}", "            pass", "call: a non-object answer crashes the call"),
    # --- normalisers -----------------------------------------------------------------------------------
    (N, "    if value > today + timedelta(days=max_future_days):", "    if value >= today + timedelta(days=max_future_days):", "dates: a day ahead is refused"),
    (N, "        if n > MAX_DAYS_AGO:", "        if n >= MAX_DAYS_AGO:", "dates: '31 days ago' is refused"),
    (N, "        if paisa > MAX_AMOUNT_PAISA:\n            raise MoneyError(\"too_large\")", "        if False:\n            raise MoneyError(\"too_large\")", "amounts: no upper limit after a multiplier"),
    (N, 'pattern = r"(?<!\\w)(?<!\\d[.,])" + re.escape(needle) + r"(?!\\w)(?![.,]\\d)"', 'pattern = r"(?<!\\w)" + re.escape(needle) + r"(?!\\w)(?![.,]\\d)"', "cross-check: '500' is found inside '1,500'"),
    (N, 'pattern = r"(?<!\\w)(?<!\\d[.,])" + re.escape(needle) + r"(?!\\w)(?![.,]\\d)"', 'pattern = r"(?<!\\w)(?<!\\d[.,])" + re.escape(needle) + r"(?!\\w)"', "cross-check: '50' is found inside '50.5'"),
    (N, 'pattern = r"(?<!\\w)(?<!\\d[.,])" + re.escape(needle) + r"(?!\\w)(?![.,]\\d)"', 'pattern = re.escape(needle)', "cross-check: any substring counts"),
    (N, "        start = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)", "        start = today + timedelta(days=today.weekday()) + timedelta(weeks=offset)", "periods: the week starts on the wrong day"),
    (N, '"kal": ("day", -1), "کل": ("day", -1),', '"kal": ("day", 1), "کل": ("day", 1),', "periods: 'kal' means tomorrow"),
    (N, "_YESTERDAY = frozenset({", "_YESTERDAY = frozenset({\"parson\", ", "dates: 'parson' means yesterday") ,
    (N, "        paisa = hundredths * _MULTIPLIERS[tokens[1]]", "        paisa = hundredths + _MULTIPLIERS[tokens[1]]", "amounts: a multiplier is added instead of multiplied"),
    # --- chat route, API, schema ----------------------------------------------------------------------
    (C, "    if uid is not None:\n        try:\n            handled = await expense_ai.handle(", "    if True:\n        try:\n            handled = await expense_ai.handle(", "chat: guests reach the assistant"),
    (C, "        if handled is not None:\n            return ChatResponse(", "        if False:\n            return ChatResponse(", "chat: a handled message also goes to the chatbot, history and voice"),
    (C, "            if expense_ai.looks_like_expense_talk(request.message):", "            if False:", "chat: after a crash the expense text goes to the chatbot"),
    (R, "def confirm(card_id: str, db: Session = Depends(get_db), user: models.User = Depends(write_user)):", "def confirm(card_id: str, db: Session = Depends(get_db), user: models.User = Depends(current_user)):", "api: confirm needs no anti-forgery header"),
    (R, "def cancel(card_id: str, db: Session = Depends(get_db), user: models.User = Depends(write_user)):", "def cancel(card_id: str, db: Session = Depends(get_db), user: models.User = Depends(current_user)):", "api: cancel needs no anti-forgery header"),
    (R, "def undo(card_id: str, db: Session = Depends(get_db), user: models.User = Depends(write_user)):", "def undo(card_id: str, db: Session = Depends(get_db), user: models.User = Depends(current_user)):", "api: undo needs no anti-forgery header"),
    (R, "def choose(card_id: str, body: ChooseBody, db: Session = Depends(get_db), user: models.User = Depends(write_user)):", "def choose(card_id: str, body: ChooseBody, db: Session = Depends(get_db), user: models.User = Depends(current_user)):", "api: choose needs no anti-forgery header"),
    (R, '"not_found": 404,', '"not_found": 403,', "api: another person's card is 403 (reveals it exists)"),
    (R, '"bad_choice": 422,', '"bad_choice": 409,', "api: a refused choice has the wrong status"),
    (R, '    model_config = ConfigDict(extra="forbid")\n    item: StrictInt', "    item: StrictInt", "api: choose accepts unknown fields"),
    (R, "    item: StrictInt\n    category_slug: StrictStr", "    item: int\n    category_slug: str", "api: choose accepts a text item number"),
    # --- migration and models ---------------------------------------------------------------------------
    (M, '        sa.UniqueConstraint("user_id", "client_msg_id", name="uq_pending_user_msg"),\n        sa.CheckConstraint("kind', '        sa.CheckConstraint("kind', "migration: no unique (user, message) rule"),
    (M, 'sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),\n        sa.Column("kind"', 'sa.ForeignKey("users.id"), nullable=False),\n        sa.Column("kind"', "migration: cards do not cascade on account deletion"),
    (M, 'sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),\n        sa.Column("period"', 'sa.ForeignKey("users.id"), primary_key=True),\n        sa.Column("period"', "migration: counters do not cascade on account deletion"),
    (M, 'sa.CheckConstraint("status IN (\'pending\',\'confirmed\',\'cancelled\',\'expired\')", name="ck_pending_status"),', 'sa.CheckConstraint("status IN (\'pending\',\'confirmed\',\'cancelled\',\'expired\',\'x\')", name="ck_pending_status"),', "migration: the status rule allows other values"),
    (MO, 'user_id       = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)\n    kind ', 'user_id       = Column(Integer, ForeignKey("users.id"), nullable=False)\n    kind ', "models: the card model does not cascade"),
]


def _make_writable_and_retry(func, path, _exc_info):
    """Windows: folders copied from a read-only (or OneDrive) source keep that flag and rmtree refuses them."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def rmtree(path) -> None:
    shutil.rmtree(path, onerror=_make_writable_and_retry)


def run_tests(repo: Path, pg: bool = False) -> bool:
    cmd = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"]
    cmd += (["-m", "pg"] + PG_TESTS) if pg else TESTS
    try:
        return subprocess.run(cmd, cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240).returncode == 0
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

    have_pg = bool(os.getenv("DATABASE_URL_TEST"))
    survived, ran, skipped = [], 0, []
    tmp = tempfile.mkdtemp(prefix="mut04b_")
    try:
        base = Path(tmp) / "base"
        shutil.copytree(ROOT, base, ignore=IGNORE)
        (base / "knowledge_base").mkdir(exist_ok=True)
        if not run_tests(base):
            print("The unmodified tests already fail; fix that first.")
            return 2
        for rel, old, new, label in chosen:
            pg = label.startswith("pg:")
            if pg and not have_pg:
                skipped.append(label)
                print("NOT RUN  " + label + "  (set DATABASE_URL_TEST to a throwaway PostgreSQL)", flush=True)
                continue
            work = Path(tmp) / "work"
            if work.exists():
                rmtree(work)
            shutil.copytree(base, work)
            text, _ = _apply((work / rel).read_text(encoding="utf-8"), old, new, label)
            (work / rel).write_text(text, encoding="utf-8")
            caught = not run_tests(work, pg)
            ran += 1
            print(("CAUGHT   " if caught else "SURVIVED ") + label, flush=True)
            if not caught:
                survived.append(label)
    finally:
        try:
            rmtree(tmp)
        except OSError:
            print(f"(could not delete the temporary folder {tmp}; you can delete it by hand)")
    print(f"\n{ran - len(survived)}/{ran} mutations caught" + (f" ({len(skipped)} need PostgreSQL and were not run)" if skipped else ""))
    if survived:
        print("Survivors:\n  " + "\n  ".join(survived))
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
