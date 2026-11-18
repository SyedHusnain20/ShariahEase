"""
Step 4a spikes: scoring and verdicts. Pure functions, no network (tested offline in tests/test_spikes.py).

Money is judged by the repo's own strict parser (app/services/money.py), the same gate the real build will use.
"""
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.money import MoneyError, parse_rupees_to_paisa   # noqa: E402

from spike_schema import VARIANT_ORDER, Understanding, variant_model, variant_style   # noqa: E402

SCHEMA_TOKENS_THRESHOLD = 50     # S1: more than this many extra prompt tokens means the schema is counted as input


def amount_verbatim(message: str, amount_text: str) -> bool:
    """The model must copy the amount from the message. Anything that is not literally in it is a rewrite."""
    return bool(amount_text) and amount_text in message


def parse_paisa(amount_text: str):
    try:
        return parse_rupees_to_paisa(amount_text)
    except MoneyError:
        return None


def _blank(value):
    return None if value in ("", "none", "unknown", None) else value


def from_sentinels(data: dict) -> dict:
    """Turn the "nothing is null" form back into the normal one: "" / "none" / "unknown" become null, and query and
    edit become null unless the message really is a query or an edit. (This is the code the real build will need.)"""
    out = dict(data)
    out["items"] = [{**i, "note": _blank(i.get("note")), "date_text": _blank(i.get("date_text")),
                     "payment_method": _blank(i.get("payment_method"))} for i in data.get("items", [])]
    q, e = data.get("query") or {}, data.get("edit") or {}
    out["query"] = None if data.get("intent") != "expense_query" else {
        "period_text": _blank(q.get("period_text")), "category_slug": _blank(q.get("category_slug")), "group_by": q.get("group_by")}
    out["edit"] = None if e.get("action") in (None, "none") else {
        "action": e.get("action"), "target_text": e.get("target_text", ""), "new_amount_text": _blank(e.get("new_amount_text")),
        "new_category_slug": _blank(e.get("new_category_slug"))}
    return out


def parse_understanding(content, variant: str = "current"):
    """The answer as the normal Understanding model, whichever schema variant produced it."""
    if not isinstance(content, str) or not content.strip():
        return None, "empty answer"
    try:
        parsed = variant_model(variant).model_validate_json(content)
        if variant_style(variant) == "sentinel":
            return Understanding.model_validate(from_sentinels(parsed.model_dump(mode="json"))), None
        return parsed, None
    except Exception as exc:                       # pydantic ValidationError or bad JSON
        return None, type(exc).__name__


def score_message(gold: dict, content, variant: str = "current") -> dict:
    out = {"id": gold["id"], "valid": False, "intent_ok": False, "count_ok": False, "items": [], "error": None}
    understanding, err = parse_understanding(content, variant)
    if understanding is None:
        out["error"] = err
        return out
    out["valid"] = True
    out["intent_ok"] = understanding.intent == gold["intent"]
    out["count_ok"] = len(understanding.items) == len(gold["items"])
    for (_, want_paisa, want_slug), got in zip(gold["items"], understanding.items):
        slug = got.category_slug.value
        paisa = parse_paisa(got.amount_text)
        out["items"].append({
            "verbatim": amount_verbatim(gold["text"], got.amount_text),
            "value_ok": None if gold["normaliser"] else paisa == want_paisa,   # words and "5k" are judged separately
            "parser_rejects": paisa is None,
            "slug_ok": slug == want_slug,
            "parent_ok": slug.split(".")[0] == want_slug.split(".")[0],
        })
    return out


def percentile(values, p):
    vals = sorted(values)
    if not vals:
        return None
    return vals[min(len(vals) - 1, max(0, math.ceil(p / 100 * len(vals)) - 1))]


def _rate(num, den):
    return None if not den else round(100 * num / den, 1)


def summarize_scores(scores: list, latencies=(), totals=()) -> dict:
    items = [i for s in scores for i in s["items"]]
    judged = [i for i in items if i["value_ok"] is not None]
    n = len(scores)
    return {
        "messages": n,
        "valid_json_pct": _rate(sum(s["valid"] for s in scores), n),
        "intent_pct": _rate(sum(s["intent_ok"] for s in scores), n),
        "item_count_pct": _rate(sum(s["count_ok"] for s in scores if s["valid"]), sum(s["valid"] for s in scores)),
        "amount_copied_pct": _rate(sum(i["verbatim"] for i in items), len(items)),
        "amount_value_pct": _rate(sum(i["value_ok"] for i in judged), len(judged)),
        "amounts_parser_rejects": sum(i["parser_rejects"] for i in items),
        "category_top1_pct": _rate(sum(i["slug_ok"] for i in items), len(items)),
        "category_group_pct": _rate(sum(i["parent_ok"] for i in items), len(items)),
        "latency_p50_s": None if not list(latencies) else round(percentile(list(latencies), 50), 2),
        "latency_p95_s": None if not list(latencies) else round(percentile(list(latencies), 95), 2),
        "tokens_per_call_avg": None if not list(totals) else round(sum(totals) / len(list(totals))),
    }


# --------------------------------------------------------------------------------------------------
# Verdicts (thresholds are this step's judgement, stated in the README)
# --------------------------------------------------------------------------------------------------
def s1_verdict(prompt_none, prompt_trivial, prompt_full):
    """Does the schema count as input tokens? Compares prompt_tokens of the same message with and without a schema."""
    if None in (prompt_none, prompt_full):
        return {"known": False, "counts_as_input": None, "delta_full": None, "delta_trivial": None}
    delta_full = prompt_full - prompt_none
    delta_trivial = None if prompt_trivial is None else prompt_trivial - prompt_none
    return {"known": True, "counts_as_input": delta_full > SCHEMA_TOKENS_THRESHOLD,
            "delta_full": delta_full, "delta_trivial": delta_trivial}


def s2_verdict(stage_ok: dict, variant_ok: dict, urdu_scores: list) -> dict:
    """STOP when the basic stage fails, when NO way of writing the schema is accepted, or when an Urdu answer is invalid.
    A variant that is refused is information (it tells us what Groq rejects), not a stop. The recommended variant is the
    first accepted one in the order sentinel, anyof_text, typearray, current."""
    failed_stages = [name for name, ok in stage_ok.items() if not ok]
    accepted = [name for name in VARIANT_ORDER if variant_ok.get(name)]
    invalid = [s["id"] for s in urdu_scores if not s["valid"]]
    items = [i for s in urdu_scores for i in s["items"]]
    copied = _rate(sum(i["verbatim"] for i in items), len(items))
    if "trivial" in failed_stages or not accepted or invalid:
        verdict = "STOP"
    elif copied is not None and copied < 100:
        verdict = "WARN"
    else:
        verdict = "PASS"
    return {"verdict": verdict, "failed_stages": failed_stages, "accepted_variants": accepted,
            "recommended_variant": accepted[0] if accepted else None,
            "refused_variants": [n for n in VARIANT_ORDER if n in variant_ok and not variant_ok[n]],
            "invalid_urdu_answers": invalid, "urdu_amount_copied_pct": copied}


def recommended_token_cap(completions: list, floor: int = 600) -> int:
    """About 1.5 times the largest answer seen, rounded up to 100, never below the floor."""
    if not completions:
        return floor
    return max(floor, int(math.ceil(max(completions) * 1.5 / 100.0)) * 100)
