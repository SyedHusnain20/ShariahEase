"""
Mutation check for Path step 5a (the AI query path, the edit/delete path and the "which one?" cards).

Idea: copy the repo to a temp folder, break ONE thing in the copy, run the step 5a tests, and require them to FAIL. A mutation
that survives means a test is missing. Mutations labelled "pg:" need a real PostgreSQL (DATABASE_URL_TEST, a throwaway database).
A mutation may be a list of (old, new) pairs: used where two defences give the same answer, so that breaking one alone is an
"equivalent mutant" (the other still catches it) and both must be broken together.

    python scripts/mutation_check_step05a.py            # all mutations
    python scripts/mutation_check_step05a.py --check    # only verify that every mutation applies exactly once
    python scripts/mutation_check_step05a.py --slice 0:20
    python scripts/mutation_check_step05a.py --only query
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
TESTS = ["tests/test_expense_query_edit.py", "tests/test_expense_ai_5a_rules.py", "tests/test_expense_ai_gold.py",
         "tests/test_pending_actions.py", "tests/test_expense_ai_flow.py", "tests/test_expense_ai_rules.py",
         "tests/test_expense_understand.py", "tests/test_migrations.py", "tests/test_account_deletion.py",
         "tests/test_privacy_and_settings.py"]
PG_TESTS = ["tests/test_edit_card_pg.py"]
Q, E, A, P, U, R = ("app/services/expense_query.py", "app/services/expense_edit.py", "app/services/expense_ai.py",
                    "app/services/pending_actions.py", "app/services/expense_understand.py", "app/routers/expense_ai.py")

# (file, old, new, label)    old/new may each be a tuple of strings (applied in order, each exactly once)
MUTATIONS = [
    # --- query: the period and the numbers --------------------------------------------------------------
    (Q, 'if period_text and fold(period_text) not in fold(message):', 'if False:', "query: period words the person never wrote are accepted"),
    (Q, '    if window is None:\n        return None, "period"', '    if False:\n        return None, "period"', "query: an unreadable period is not refused"),
    (Q, ('    if slug is not None and slug not in names:\n        return None, "category"', '    except es.InvalidInput:\n        return None, "category"'),
        ('    if False:\n        return None, "category"', '    except es.InvalidInput:\n        raise'),
        "query: an unknown category is not refused (both checks removed)"),
    (Q, 'hit = next((g for g in summary.groups if g.key == slug), None)', 'hit = next((g for g in summary.groups), None)', "query: one sub-category answer takes the wrong group"),
    (Q, 'hidden=max(0, len(groups) - len(shown))', 'hidden=0', "query: hidden groups are not counted"),
    (Q, 'groups[:MAX_GROUP_LINES]', 'groups[:MAX_GROUP_LINES + 1]', "query: more group lines than the limit"),
    (Q, 'return self.date_to - timedelta(days=1)', 'return self.date_to', "query: the last day shown is the day after"),
    (Q, 'return numbers_in(answer) - numbers_in(data_text)', 'return set()', "query: tripwire never finds a number"),
    (Q, 'plain = raw.replace(",", "")', 'plain = raw.replace(",", "").replace(".", "")', "query: tripwire drops the decimal point (100x blind spot)"),
    (Q, 'return format(Decimal(plain).normalize(), "f")', 'return plain', "query: tripwire compares text, not numbers"),
    (Q, 'r"\\d+(?:[.,]\\d+)*"', 'r"\\d+"', "query: tripwire splits numbers at separators"),
    # --- the assistant: query and edit wiring ----------------------------------------------------------
    (A, 'if unexpected_numbers(answer, query_data_text(result)):', 'if False:', "assistant: a query answer with a foreign number is shown"),
    (A, 'parts = [result.date_from.isoformat(), result.last_day.isoformat(), _money(result.total_paisa), str(result.count), str(result.hidden)]',
        'parts = [result.date_from.isoformat(), result.last_day.isoformat(), str(result.count), str(result.hidden)]', "assistant: the data list misses the total"),
    (A, '        ai_limits.release(db, user_id, period)\n        return Handled(_say("q_category" if problem == "category" else problem, lang), kind="info")',
        '        return Handled(_say("q_category" if problem == "category" else problem, lang), kind="info")', "assistant: a refused question costs the allowance"),
    (A, '        ai_limits.release(db, user_id, period)\n        return Handled(_say(outcome.problem, lang), kind="info")',
        '        return Handled(_say(outcome.problem, lang), kind="info")', "assistant: a refused edit costs the allowance"),
    (A, '    row, created = pa.create_card(db, user_id, client_msg_id=msg_id, payload=outcome.payload, now=now)\n    if not created:\n        ai_limits.release(db, user_id, period)',
        '    row, created = pa.create_card(db, user_id, client_msg_id=msg_id, payload=outcome.payload, now=now)\n    if not created:\n        pass', "assistant: a parallel copy of an edit message is charged"),
    (A, '    return Handled(answer, kind="answer")', '    return Handled(answer + " " + (understood.query.period_text or ""), kind="answer")', "assistant: the person's own words are added to a query answer"),
    (A, '        return None                                             # a lone digit with no list waiting is not an expense: no model call\n', '', "assistant: a lone digit with no list waiting goes to the model"),
    (A, 'if waiting is not None and waiting.kind != "create":\n            return _answer_pick', 'if waiting is not None:\n            return _answer_pick', "assistant: a number picks on a plain expense card"),
    (A, "r\"[0-9]\", word", "r\"[0-9]{1,3}\", word", "assistant: a long number counts as a pick"),
    (A, 'if not 1 <= number <= len(payload.candidates):', 'if number > len(payload.candidates):', "assistant: zero is a valid pick"),
    (A, 'card = pa.render(row, rows) if err.code == "not_chosen" else None', 'card = None', "assistant: no list is shown when yes comes before the pick"),
    (A, 'for i, t in enumerate(payload.candidates, start=1)', 'for i, t in enumerate(payload.candidates, start=0)', "assistant: the list starts at 0"),
    (A, '        return _say("pick_more", lang, n=len(payload.candidates)) + "\\n" + text if truncated else text', '        return text', "assistant: the list never says more entries fit") if False else
    (A, 'return _say("pick_more", lang, n=len(payload.candidates)) + "\\n" + text if truncated else text', 'return text', "assistant: the list never says more entries fit"),
    (A, 'return _say("edit_delete", lang, before=before)', 'return _say("edit_delete", lang, before=before) + " " + (target.note or "")', "assistant: the note of an entry is put in the reply text"),
    (A, 'return edit_line(target, rows, lang, amount_paisa=changes.new_amount_paisa, category_slug=changes.new_category_slug)',
        'return edit_line(target, rows, lang)', "assistant: the 'after' line shows the old values"),
    (A, 'if row.kind == "delete":\n        return Handled(_say("deleted"', 'if row.kind == "update":\n        return Handled(_say("deleted"', "assistant: a changed entry is reported as deleted"),
    # --- the edit resolver -------------------------------------------------------------------------------
    (E, '    if message_words & _BULK:\n        return EditOutcome("one_at_a_time")', '    if False:\n        return EditOutcome("one_at_a_time")', "edit: 'delete all' is accepted"),
    (E, '    if not set(target_words) <= message_words:', '    if False:', "edit: target words the person never wrote are accepted"),
    (E, '    if crit.many:\n        return EditOutcome("unclear_target")', '    if False:\n        return EditOutcome("unclear_target")', "edit: two amounts in the target are not unclear"),
    (E, '            if not amount_in_message(edit.new_amount_text, message):', '            if False:', "edit: a new amount the model made up is accepted"),
    (E, '        if new_category is not None and new_category not in {r[0] for r in rows}:', '        if False:', "edit: a category that does not exist is accepted"),
    (E, '        if new_amount is None and new_category is None:\n            return EditOutcome("need_change")', '        if False:\n            return EditOutcome("need_change")', "edit: an update with no change is accepted"),
    (E, '    if edit.action not in ("update", "delete"):\n        return EditOutcome("need_change")', '    if False:\n        return EditOutcome("need_change")', "edit: no action is accepted"),
    (E, '    chosen = 0 if len(found) == 1 and not relaxed and not crit.empty else None', '    chosen = 0 if len(found) == 1 and not crit.empty else None', "edit: words that fitted nothing still choose the entry"),
    (E, '    chosen = 0 if len(found) == 1 and not relaxed and not crit.empty else None', '    chosen = 0 if len(found) == 1 and not relaxed else None', "edit: no words at all still choose the entry"),
    (E, '    chosen = 0 if len(found) == 1 and not relaxed and not crit.empty else None', '    chosen = 0 if len(found) >= 1 and not relaxed and not crit.empty else None', "edit: the first of several matches is chosen"),
    (E, 'return matches[:pa.MAX_CANDIDATES], len(matches) > pa.MAX_CANDIDATES, relaxed', 'return matches[:50], len(matches) > 50, relaxed', "edit: more than five candidates are shown"),
    (E, '        matches = [max(matches, key=lambda e: (e.created_at, e.id))]', '        matches = [min(matches, key=lambda e: (e.created_at, e.id))]', "edit: 'last' means the oldest"),
    (E, '    if crit.last and matches:', '    if False:', "edit: 'last' is ignored"),
    (E, '"date_to": crit.day + timedelta(days=1)}', '"date_to": crit.day}', "edit: a named day finds nothing (empty window)"),
    (E, '        if crit.amount is not None and e.amount_paisa != crit.amount:', '        if False:', "edit: the amount is ignored when looking"),
    (E, '    return fold(" ".join([e.note or "", en, ur, slug.split(".")[-1].replace("_", " ")]))', '    return fold(" ".join([en, ur, slug.split(".")[-1].replace("_", " ")]))', "edit: notes are not searched"),
    (E, '_POSSESSIVE = re.compile(r"[\'’]s$")', '_POSSESSIVE = re.compile(r"[\'’]z$")', "edit: possessives are not stripped"),
    (E, 'if j not in used and tok in _LAST and not (j + 1 < len(tokens) and tokens[j + 1] in _TIME_UNITS):', 'if j not in used and tok in _LAST:', "edit: 'last month' counts as the last entry"),
    (E, 'if not matches and crit.words and (crit.amount is not None or crit.day is not None or crit.last):', 'if False:', "edit: no relaxed search when the words fit nothing"),
    # --- cards -------------------------------------------------------------------------------------------
    (P, 'MAX_CANDIDATES = 5\n', 'MAX_CANDIDATES = 6\n', "card: six candidates allowed"),
    (P, '        if self.chosen is not None and not 0 <= self.chosen < len(self.candidates):', '        if False:', "card: a choice out of range is accepted"),
    (P, '        if self.action == "update" and not has:', '        if False:', "card: an update without a change is accepted"),
    (P, '        if self.action == "delete" and self.changes is not None:', '        if False:', "card: a delete with a change is accepted"),
    (P, 'kind = "create" if isinstance(payload, CardPayload) else payload.action', 'kind = "create"', "card: an edit card is stored as a create card"),
    (P, '"needs_choice": payload.chosen is None', '"needs_choice": False', "card: the card never says a choice is needed"),
    (P, ('    if EditPayload.model_validate(row.payload).chosen is None:\n        raise CardError("not_chosen")\n',
         '        if payload.chosen is None:                      # never act without a target the person named\n            db.rollback()\n            raise CardError("not_chosen")\n',
         '        target = payload.candidates[payload.chosen]\n        current = _current'),
        ('', '', '        target = payload.candidates[payload.chosen or 0]\n        current = _current'),
        "card: an unchosen card acts on the first candidate (both checks removed)"),
    (P, 'if current is None or current.version != target.version:', 'if current is None:', "card: an entry changed since the card is still changed"),
    (P, 'es.delete_expenses(db, user_id, [target.expense_id], actor="ai", commit=False)', 'es.delete_expenses(db, user_id, [target.expense_id], actor="user", commit=False)', "card: an AI delete is audited as the user"),
    (P, 'out = es.update_expense(db, user_id, target.expense_id, target.version, changes, actor="ai", commit=False)', 'out = es.update_expense(db, user_id, target.expense_id, target.version, changes, actor="user", commit=False)', "card: an AI update is audited as the user"),
    (P, '.values(status="cancelled", resolved_at=now).execution_options(synchronize_session=False))\n        db.commit()\n    except Exception:', '.values(resolved_at=now).execution_options(synchronize_session=False))\n        db.commit()\n    except Exception:', "card: a stale card stays open"),
    (P, '                PendingAction.status == "pending",\n                PendingAction.expires_at > now,\n            )\n            .values(status="confirmed", resolved_at=now)', '                PendingAction.expires_at > now,\n            )\n            .values(status="confirmed", resolved_at=now)', "card: an edit confirm does not require pending"),
    (P, '                PendingAction.expires_at > now,\n            )\n            .values(status="confirmed", resolved_at=now)', '            )\n            .values(status="confirmed", resolved_at=now)', "card: an edit confirm ignores expiry"),
    # (dropped: 'an edit confirm does not check the owner in the claim' is an equivalent mutant: the next step, expense_service's
    #  per-user lookup, finds nothing for another person and the claim is rolled back. Two defences give the same answer.)
    (P, '                                 PendingAction.rev == row.rev,\n                             )', '                             )', "card: a parallel pick can overwrite another"),
    (P, '                                 PendingAction.status == "pending",\n                                 PendingAction.expires_at > now,\n                                 PendingAction.rev == row.rev,', '                                 PendingAction.expires_at > now,\n                                 PendingAction.rev == row.rev,', "card: a used card can still be picked"),
    (P, '                                 PendingAction.user_id == user_id,\n                                 PendingAction.status == "pending",\n                                 PendingAction.expires_at > now,\n                                 PendingAction.rev == row.rev,', '                                 PendingAction.status == "pending",\n                                 PendingAction.expires_at > now,\n                                 PendingAction.rev == row.rev,', "card: the owner is not checked in the pick update"),
    (P, '        if row.kind == "create":\n            raise CardError("bad_choice")\n        payload = EditPayload.model_validate(row.payload)', '        payload = EditPayload.model_validate(row.payload)', "card: pick works on a plain expense card"),
    (P, '        if row.kind != "create":\n            raise CardError("bad_choice")\n        payload = CardPayload.model_validate(row.payload)', '        payload = CardPayload.model_validate(row.payload)', "card: the category choice works on an edit card"),
    (P, 'if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(payload.candidates):', 'if not 0 <= index < len(payload.candidates):', "card: a boolean index is a valid pick"),
    (P, 'changed = es.restore_expenses(db, user_id, ids, actor="user", commit=False).changed if ids else []', 'changed = es.delete_expenses(db, user_id, ids, actor="user", commit=False).changed if ids else []', "undo: a delete is 'undone' by deleting again"),
    (P, '                es.update_expense(db, user_id, ids[0], result["version_after"], back, actor="user", commit=False)', '                es.update_expense(db, user_id, ids[0], 99999, back, actor="user", commit=False)', "undo: an update can never be undone"),
    (P, '            except (es.VersionConflict, es.NotFound):\n                db.rollback()\n                raise CardError("stale")\n            changed = [ids[0]]', '            except (es.VersionConflict, es.NotFound):\n                pass\n            changed = [ids[0]]', "undo: a later change is reported as undone"),
    (P, '                back["amount_paisa"] = target.amount_paisa', '                back["amount_paisa"] = payload.changes.new_amount_paisa', "undo: the amount is not put back"),
    (P, '                back.update(_category_fields(target.category_slug))', '                back.update(_category_fields(payload.changes.new_category_slug))', "undo: the category is not put back"),
    # --- the model form and the prompt ----------------------------------------------------------------
    (U, 'period_text=blank(form.query.period_text),', 'period_text=None,', "understand: the period words are dropped"),
    (U, 'group_by=form.query.group_by,', 'group_by="none",', "understand: the grouping is dropped"),
    (U, 'category_slug=None if form.query.category_slug.value == "none" else form.query.category_slug.value,\n            group_by', 'category_slug=None,\n            group_by', "understand: the query category is dropped"),
    (U, 'target_text=blank(form.edit.target_text),', 'target_text=None,', "understand: the target words are dropped"),
    (U, 'new_amount_text=blank(form.edit.new_amount_text),', 'new_amount_text=None,', "understand: the new amount is dropped"),
    (U, 'new_category_slug=None if form.edit.new_category_slug.value == "none" else form.edit.new_category_slug.value,', 'new_category_slug=None,', "understand: the new category is dropped"),
    (U, 'action=form.edit.action,', 'action="none",', "understand: the edit action is dropped"),
    (U, '10. For expense_query: period_text is the period words copied exactly', '10. For expense_query: nothing special', "prompt: the query rule is missing"),
    (U, '11. For expense_edit_delete: action is delete or update.', '11. For expense_edit_delete: act.', "prompt: the edit rule is missing"),
    # --- the routes -------------------------------------------------------------------------------------
    (R, '    index: StrictInt\n', '    index: int\n', "route: the pick index may be a boolean or a float"),
    (R, 'class PickBody(BaseModel):\n    model_config = ConfigDict(extra="forbid")', 'class PickBody(BaseModel):\n    model_config = ConfigDict(extra="allow")', "route: the pick body accepts unknown fields"),
    (R, '"stale": 409, "not_chosen": 409}', '"stale": 422, "not_chosen": 422}', "route: stale and not-chosen are not conflicts"),
    (R, '"kind": row.kind if row is not None else "create"', '"kind": "create"', "route: a confirm does not say which kind it was"),
    # --- Postgres only --------------------------------------------------------------------------------
    (P, '            .where(\n                PendingAction.id == row.id,\n                PendingAction.user_id == user_id,\n                PendingAction.status == "pending",\n                PendingAction.expires_at > now,\n            )\n            .values(status="confirmed", resolved_at=now)\n            .execution_options(synchronize_session=False)\n            .returning(PendingAction.payload)\n        ).first()\n        if claimed is None:\n            db.rollback()\n            raise _why_not(db, user_id, row.id, now)\n        payload = EditPayload.model_validate(claimed[0])',
        '            .where(\n                PendingAction.id == row.id,\n                PendingAction.user_id == user_id,\n                PendingAction.expires_at > now,\n            )\n            .values(status="confirmed", resolved_at=now)\n            .execution_options(synchronize_session=False)\n            .returning(PendingAction.payload)\n        ).first()\n        if claimed is None:\n            db.rollback()\n            raise _why_not(db, user_id, row.id, now)\n        payload = EditPayload.model_validate(claimed[0])',
        "pg: 30 parallel edit confirms all pass the claim"),
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
    tmp = tempfile.mkdtemp(prefix="mut05a_")
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
