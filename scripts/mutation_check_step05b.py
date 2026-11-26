"""
Mutation check for Path step 5b (the chat-page expense UI and the "Notify me about Pro" button).

Idea: copy the repo to a temp folder, break ONE thing in the copy, run the step 5b tests, and require them to FAIL. A mutation
that survives means a test is missing. Mutations labelled "pg:" need a real PostgreSQL (DATABASE_URL_TEST, a throwaway database).
Equivalent mutants are left out on purpose: the `db.rollback()` after "already recorded" in upgrade_interest.py only releases the
row lock a moment earlier (the session is closed right after anyway), so no test can tell the difference.

    python scripts/mutation_check_step05b.py            # all mutations
    python scripts/mutation_check_step05b.py --check    # only verify that every mutation applies exactly once
    python scripts/mutation_check_step05b.py --slice 0:20
    python scripts/mutation_check_step05b.py --only Pro

Needs Node on the PATH for the JS tests (without Node they are skipped and their mutations survive: the script says so).
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
TESTS = ["tests/test_upgrade_interest.py", "tests/test_expense_chat_js.py", "tests/test_expense_chat_ui.py",
         "tests/test_privacy_and_settings.py", "tests/test_expense_pwa.py", "tests/test_account_deletion.py",
         "tests/test_expense_ai_flow.py"]
PG_TESTS = ["tests/test_upgrade_interest_pg.py"]
U, R, P, J, H, S, V = ("app/services/upgrade_interest.py", "app/routers/expense_ai.py", "app/routers/pages.py",
                       "frontend/static/js/expense_chat.js", "frontend/templates/pages/chatbot.html", "frontend/static/sw.js",
                       "frontend/templates/pages/privacy.html")

# (file, old, new, label)    old/new may each be a tuple of strings (applied in order, each exactly once)
MUTATIONS = [
    # --- the Pro button: service ---------------------------------------------------------------------------
    (U, 'ACTION = "upgrade_interest"', 'ACTION = "upgrade_intrest"', "pro: the action name is wrong (the gate would count nobody)"),
    (U, 'actor="user",', 'actor="system",', "pro: the row is written by another actor"),
    (U, 'entity_type=None,', 'entity_type="expense",', "pro: the row names an entity type"),
    (U, 'entity_id=None,', 'entity_id="1",', "pro: the row names an entity"),
    (U, 'detail=null()', 'detail={"count": 1}', "pro: the row carries detail"),
    (U, 'AuditLog.user_id == user_id, AuditLog.action == ACTION', 'AuditLog.action == ACTION', "pro: one person's tap counts for everybody"),
    (U, 'AuditLog.user_id == user_id, AuditLog.action == ACTION', 'AuditLog.user_id == user_id', "pro: any activity row counts as having tapped"),
    (U, '    if has_recorded(db, user_id):', '    if False:', "pro: a second tap writes a second row"),
    (U, '    db.commit()\n    return True', '    return True', "pro: the row is never committed"),
    (U, 'db.add(AuditLog(user_id=user_id,', 'db.add(AuditLog(user_id=user_id + 1,', "pro: the row is written for another person"),
    (U, ').where(User.id == user_id).with_for_update()', ').where(User.id == user_id)', "pg: pro: two taps at the same moment are not serialised"),
    # --- the Pro button: route, status, privacy, service worker ----------------------------------------------
    (R, '"recorded" if first else "already_recorded"', '"recorded"', "pro route: a second tap is also called 'recorded'"),
    (R, 'def upgrade_interest_tap(db: Session = Depends(get_db), user: models.User = Depends(write_user)):', 'def upgrade_interest_tap(db: Session = Depends(get_db), user: models.User = Depends(current_user)):',
        "pro route: no write header and no write limit"),
    (R, 'upgrade_interest.record_upgrade_interest(db, user.id)', 'upgrade_interest.record_upgrade_interest(db, 1)', "pro route: always person 1"),
    (R, '"upgrade_interest": upgrade_interest.has_recorded(db, user.id)', '"upgrade_interest": False', "pro route: status never says the person already tapped"),
    (R, '"upgrade_interest": upgrade_interest.has_recorded(db, user.id)', '"upgrade_interest": upgrade_interest.has_recorded(db, 1)', "pro route: status shows person 1's tap to everybody"),
    (P, 'PRIVACY_UPDATED = "26 November 2026"', 'PRIVACY_UPDATED = "24 November 2026"', "privacy: the date was not moved"),
    (V, '<dt class="font-semibold text-on-surface">Pro interest</dt>', '<dt class="font-semibold text-on-surface">Interest</dt>', "privacy: the Pro entry is gone"),
    (V, 'no email address, no phone number and no payment details are asked for or kept', 'your email address is kept', "privacy: the Pro entry promises less than the code does"),
    (V, 'It is part of your activity log and is deleted with your account.', 'It is kept.', "privacy: the Pro entry does not say it is deleted with the account"),
    (V, 'Your saved expenses are not sent to any AI or search service, and they are never written to our logs. (If you use the AI assistant in chat, the message you type is sent to our AI provider so it can be read: see "AI assistant confirmation cards" below.)',
        'Your expenses are not sent to any AI or search service, and they are never written to our logs. (The expense tracker has no AI feature yet. If that changes, this page changes first.)', "privacy: the old false sentence comes back"),
    (S, "const VERSION = 'v3';", "const VERSION = 'v2';", "sw: the cache version was not bumped"),
    (S, "  '/static/js/expense_chat.js',\n", "", "sw: the new file is not precached"),
    # --- the chat page: wiring ------------------------------------------------------------------------------
    (H, '            client_msg_id: clientMsgId,\n', '', "page: the send has no client_msg_id"),
    (H, '        this.xAfterReply(data, this.formatTime());', "        this.messages.push({ role: 'assistant', type: 'text', content: data.answer, time: this.formatTime(), sources: data.sources || [] });", "page: the reply is not handed to the card code"),
    (H, '          signal:  ctl.signal,\n', '', "page: the send can wait for ever"),
    (H, '<script src="/static/js/expense_chat.js"></script>\n', '', "page: the expense module is not loaded"),
    (H, 'x-text="item.note"', 'x-html="item.note"', "page: the note is inserted as HTML"),
    (H, '<p class="text-body-md leading-relaxed whitespace-pre-wrap" dir="auto" style="text-align:start" x-text="msg.content"></p>', '<p class="text-body-md leading-relaxed whitespace-pre-wrap" x-text="msg.content"></p>', "page: messages lose their automatic direction (Urdu would be laid out wrongly)"),
    (H, '<button type="button" data-testid="card-confirm"', '<button data-testid="card-confirm"', "page: the confirm button is not a plain button"),
    (H, 'flex-1 min-h-[44px] rounded-xl bg-primary', 'flex-1 min-h-[30px] rounded-xl bg-primary', "page: the confirm button is smaller than 44 px"),
    (H, ':disabled="msg.busy || xNeedsPick(msg.card)"', ':disabled="msg.busy"', "page: confirm can be pressed before an entry is picked"),
    (H, 'data-testid="pro-button" @click="xPro()" :disabled="xProBusy || xProDone"', 'data-testid="pro-button" @click="xPro()" :disabled="xProDone"', "page: the Pro button can be pressed while it is sending"),
    (H, 'data-testid="pro-button-strip" @click="xPro()" :disabled="xProBusy || xProDone"', 'data-testid="pro-button-strip" @click="xPro()" :disabled="xProDone"', "page: the Pro button in the limit state can be pressed while it is sending"),
    (H, 'class="flex-1 basis-[65%] min-w-0 text-xs', 'class="flex-1 basis-[10%] min-w-0 text-xs', "page: the status text can be squeezed beside the buttons"),
    (H, "[@media(max-height:480px)]:hidden", "", "page: the status strip stays when the keyboard leaves no room"),
    (H, "max-h-[50dvh] overflow-y-auto", "", "page: the open panel can grow past the screen"),
    (H, '<div x-show="xActive(msg)" class="flex gap-2">', '<div class="flex gap-2">', "page: confirm and cancel stay on an expired card"),
    (H, 'data-testid="card-undo" x-show="xCanUndo(msg)"', 'data-testid="card-undo"', "page: undo is always shown"),
    (H, '@keydown.enter.prevent="xPickMode(msg) && xPick(msg, item.index)"', '', "page: picking works only by touch"),
    # --- the module: requests -----------------------------------------------------------------------------------
    (J, "const WRITE_HEADER = { 'X-Requested-With': 'ShariahEase' };", "const WRITE_HEADER = { 'X-Requested-With': 'ShariahEas' };", "js: the write header has the wrong value"),
    (J, "method === 'GET' ? {} : WRITE_HEADER", "method === 'GET' ? {} : {}", "js: POSTs carry no write header"),
    (J, "if (!msg || !msg.card || msg.busy) return;", "if (!msg || !msg.card) return;", "js: a second tap sends a second request"),
    (J, "          msg.busy = false;\n        }\n      },\n\n      xApplyOk", "          msg.busy = true;\n        }\n      },\n\n      xApplyOk", "js: the buttons stay disabled after a request"),
    (J, "if (action === 'undo' ? !this.xCanUndo(msg) : !this.xActive(msg)) return;", "", "js: an expired card still sends its request"),
    (J, "const UNDO_MS = 60 * 60 * 1000;", "const UNDO_MS = 60 * 1000;", "js: the undo hour is a minute"),
    (J, "const TIMEOUT_MS = 20000;", "const TIMEOUT_MS = 2000000;", "js: a request may hang for half an hour"),
    (J, "this.ct(card, unsure ? 'e_net_unsure' : 'e_net')", "this.ct(card, 'e_net')", "js: a lost connection on Confirm claims nothing happened"),
    (J, "case 'stale': stop('o_stale', 'cancelled'); return;", "case 'stale': stop('o_stale'); return;", "js: a stale card is not closed on screen"),
    (J, "case 'already_used': stop('e_used'); return;", "case 'already_used': stop('o_saved'); return;", "js: an already used card says 'saved'"),
    (J, "case 'not_chosen': msg.error = this.ct(card, 'e_not_chosen'); return;", "case 'not_chosen': stop('e_not_chosen'); return;", "js: 'choose an entry first' closes the card"),
    (J, "if (r.status === 429) {", "if (r.status === 4290) {", "js: a rate limit is shown as a plain error"),
    (J, "return h > 0 ? h : (d > 0 ? d : 60);", "return 60;", "js: the wait time of a rate limit is ignored"),
    (J, "if (r.status === 401 && handleAuthError(r.res)) return;\n          if (r.ok) { this.xApplyOk", "if (r.ok) { this.xApplyOk", "js: a 401 on a card is not sent to the login handler"),
    # --- the module: what a card offers -----------------------------------------------------------------------------
    (J, "Date.parse(card.expires_at) <= this.xClock()", "Date.parse(card.expires_at) <= Date.now()", "js: expiry ignores the server's clock"),
    (J, "this.xOffset = t - Date.now();", "this.xOffset = Date.now() - t;", "js: the clock difference has the wrong sign"),
    (J, "msg.card.status === 'pending' && !msg.outcome && !msg.superseded && !this.xExpiredNow(msg.card)", "msg.card.status === 'pending' && !msg.outcome && !msg.superseded", "js: an expired card still offers buttons"),
    (J, "msg.card.status === 'pending' && !msg.outcome && !msg.superseded && !this.xExpiredNow(msg.card)", "msg.card.status === 'pending' && !msg.outcome && !this.xExpiredNow(msg.card)", "js: an older card is still offered next to the newest"),
    (J, "msg.card.status === 'pending' && !msg.outcome && !msg.superseded && !this.xExpiredNow(msg.card)", "msg.card.status === 'pending' && !msg.superseded && !this.xExpiredNow(msg.card)", "js: a finished card still offers buttons"),
    (J, "return !!(msg && msg.undoUntil && msg.undoUntil > this.xClock());", "return !!(msg && msg.undoUntil);", "js: undo stays after the hour"),
    (J, "(card.chosen === null || card.chosen === undefined)", "false", "js: an edit card can be confirmed before a pick"),
    (J, "m.superseded = true;", "m.superseded = false;", "js: a new card does not replace the older one"),
    (J, "if (r.data.card.id !== shown.card.id) { if (!shown.outcome) shown.outcome = 'o_answered'; return; }", "", "js: a newer waiting card does not close the shown one"),
    (J, "if (!msg.card) this.xReconcile();", "", "js: a typed yes is not noticed on the card"),
    (J, "if (!r.data.card) { if (!shown.outcome) shown.outcome = 'o_answered'; return; }", "if (!r.data.card) { return; }", "js: a typed yes leaves the card clickable"),
    (J, "if (!card || !card.id || card.status !== 'pending') return;", "if (!card || !card.id) return;", "js: a card that is not pending is restored as live"),
    (J, "if (this.messages.some((m) => m.card && m.card.id === card.id)) return;", "", "js: a waiting card is shown twice"),
    (J, "} else if (data.card) {            // choose, pick", "} else if (false) {            // choose, pick", "js: choosing a category does not show the new card"),
    (J, "xPick(msg, index) { return this.xAct(msg, 'pick', { index }); }", "xPick(msg, index) { return this.xAct(msg, 'pick', { index: String(index) }); }", "js: the pick index is sent as text"),
    (J, "{ item, category_slug: slug }", "{ item: item + 1, category_slug: slug }", "js: the wrong item gets the new category"),
    (J, "if (ch.category_slug) rows.push", "if (false) rows.push", "js: a category change is not shown"),
    (J, "if (ch.amount_paisa !== null && ch.amount_paisa !== undefined) rows.push", "if (false) rows.push", "js: an amount change is not shown"),
    (J, "card && card.lang === 'ur' ? item.category_name_ur : item.category_name_en", "item.category_name_en", "js: Urdu cards show English category names"),
    (J, "roman_ur: 'ro' }", "roman_ur: 'en' }", "js: Roman Urdu cards use English text"),
    (J, "timeZone: 'UTC' }", "}", "js: a date shifts with the phone's time zone"),
    # --- the module: status, language, Pro, retry -------------------------------------------------------------------
    (J, "if (this.xStatus.used >= this.xStatus.limit) return 'limit';", "if (this.xStatus.used > this.xStatus.limit) return 'limit';", "js: the limit state starts one message too late"),
    (J, "if (this.xStatus.enabled === false) return 'off';", "", "js: the assistant being off is not shown"),
    (J, "if (this.xStatus.breaker_open) return 'breaker';", "", "js: the daily limit is not shown"),
    (J, "if (r.status === 401) { this.xLoggedIn = false; return; }      // not logged in after all", "if (false) { return; }      // not logged in after all", "js: a 401 on the status leaves the panel as if logged in"),
    (J, "if (r.data.upgrade_interest) this.xProDone = true;", "", "js: the Pro button forgets that the person already tapped"),
    (J, "if (PANEL_LANGS.indexOf(lang) < 0) return;", "", "js: an unknown language is accepted"),
    (J, "if (PANEL_LANGS.indexOf(saved) >= 0) this.xLang = saved;", "if (saved) this.xLang = saved;", "js: an unknown saved language is accepted"),
    (J, "this.xLoggedIn = !!safeStorage('get', 'se_token');", "this.xLoggedIn = true;", "js: a guest is treated as logged in"),
    (J, "if (this.xProBusy || this.xProDone || !this.xLoggedIn) return;", "if (!this.xLoggedIn) return;", "js: the Pro button sends again and again"),
    (J, "if (r.ok) { this.xProDone = true; return; }", "this.xProDone = true; if (r.ok) { return; }", "js: a failed Pro tap says thanks"),
    (J, "return this.xFailedSend && this.xFailedSend.text === text ? this.xFailedSend.id : newId();", "return newId();", "js: a retry of a failed message gets a new id"),
    (J, "b[6] = (b[6] & 0x0f) | 0x40;", "b[6] = (b[6] & 0x0f);", "js: the fallback id is not a valid UUID"),
    (J, "this.inputText = text;\n        this.xOpen = false;", "this.inputText = '';\n        this.xOpen = false;", "js: an example does not fill the box"),
    (J, "this.xOpen = false;\n        const ta", "const ta", "js: the panel stays open after an example is chosen"),
    (J, "this.xOpen = false;                  // the answer (or card) must be what is on screen\n", "", "js: the panel stays open over the answer"),
    (J, "this.xOpen = false;                  // closed:", "this.xOpen = true;                  // closed:", "js: the panel starts open and covers the chat"),
    (J, "valid_until: 'یہ {time} تک درست ہے',", "valid_until: '{time} تک درست ہے',", "js: the Urdu valid-until line starts with the time (wrong direction)"),
    (J, "if (r.net || !r.ok || !r.data) return;\n        if (!r.data.card)", "if (!r.data.card)", "js: a failed check closes the card"),
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
        return subprocess.run(cmd, cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=300).returncode == 0
    except subprocess.TimeoutExpired:          # a hang is a failure too: the broken code is noticed
        return False


def _apply(text: str, old, new, label: str):
    olds, news = (old, new) if isinstance(old, tuple) else ((old,), (new,))
    for o, n in zip(olds, news):
        if o == "" and n == "":                 # a placeholder pair (kept so the tuples line up)
            continue
        if text.count(o) != 1:
            return None, f"BAD MUTATION (found {text.count(o)}x): {label}"
        text = text.replace(o, n)
    return text, None


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
    tmp = tempfile.mkdtemp(prefix="mut05b_")
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
    print(f"\n{ran - len(survived)} of {ran} caught" + (f" ({len(skipped)} need PostgreSQL and were not run)" if skipped else ""))
    if survived:
        print("Survivors:\n  " + "\n  ".join(survived))
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
