"""
Shared by tests/test_expense_ai_gold.py (offline) and scripts/score_gold_live.py (real model): put the gold set's SEED entries into
a database and replay one case through the real code that follows the model. No pytest import, no network.
"""
import uuid
from datetime import datetime, timezone

from app.services import expense_service as es
from app.services.expense_ai import build_payload
from app.services.expense_edit import build_edit
from app.services.expense_query import run_query
from app.services.expense_understand import get_contract, taxonomy, to_domain
from tests import gold_set as g

NOW = datetime(2026, 10, 31, 7, 0, tzinfo=timezone.utc)          # 12:00 on 2026-10-31 in Karachi


def build_world(db, uid):
    """Put the SEED entries into `db` for user `uid`. Returns (db, uid, rows, form_model)."""
    for rupees, slug, day, note in g.SEED:
        es.add_expenses(db, uid, [{"client_id": uuid.uuid4(), "amount_paisa": rupees * 100, "occurred_on": day,
                                   "subcategory_slug": slug, "note": note}])
    rows = taxonomy(db)
    return db, uid, rows, get_contract(rows).model


def evaluate_understood(world, understood, message):
    """What the code ends with for what the model said (an expense_understand.Understood): the same shape as Case.expect."""
    db, uid, rows, _ = world
    lang = {"mixed": "roman_ur"}.get(understood.language, understood.language)
    if understood.intent in ("islamic_finance_qa", "off_topic", "unclear"):
        return {"outcome": "passthrough"}
    if understood.intent == "expense_query":
        result, problem = run_query(db, uid, understood.query, message, g.TODAY, rows)
        if result is None:
            return {"outcome": "refused", "problem": problem}
        return {"outcome": "answer", "window": (result.date_from.isoformat(), result.date_to.isoformat()),
                "category": result.category_slug, "result": result}
    if understood.intent == "expense_edit_delete":
        out = build_edit(db, uid, understood, message, lang, g.TODAY, rows)
        if out.payload is None:
            return {"outcome": "refused", "problem": out.problem}
        return {"outcome": "edit_card", "action": out.payload.action, "candidates": len(out.payload.candidates),
                "chosen": out.payload.chosen is not None}
    payload, problem = build_payload(db, uid, understood, message, lang, NOW, rows)
    if payload is None:
        return {"outcome": "refused", "problem": problem}
    return {"outcome": "card", "items": [(i.amount_paisa, i.category_slug, i.occurred_on.isoformat(), "") for i in payload.items]}


def evaluate(world, case):
    """The ideal model answer of a case, replayed."""
    return evaluate_understood(world, to_domain(world[3].model_validate(case.model)), case.message)


def comparable(case, got):
    """Drop what the case does not state (the alternatives list, the raw query result)."""
    want = dict(case.expect)
    got = {k: v for k, v in got.items() if k != "result"}
    if want["outcome"] == "card" and got.get("outcome") == "card":
        want["items"] = [(p, sl, d) for p, sl, d, _ in want["items"]]
        got["items"] = [(p, sl, d) for p, sl, d, _ in got["items"]]
    return want, got
