"""
Step 4a: the six AI spikes. Run on YOUR machine with YOUR Groq key (the cloud sandbox cannot reach Groq).

    python scripts/spikes/run_spikes.py --dry-run            # plan only: no key needed, nothing is sent
    python scripts/spikes/run_spikes.py --only S1,S2         # the two that decide whether 4b can go ahead
    python scripts/spikes/run_spikes.py                      # everything, in order (S6 needs no key)
    python scripts/spikes/run_spikes.py --my-messages my.txt # also count YOUR real messages in S6

Options: --budget N (tokens this run may spend, default 120000)   --n N (messages per model in S4, default 20)
         --models a,b   --fresh (ignore saved answers)   --results-dir PATH   --continue-after-stop
         --variant auto|sentinel|anyof_text|typearray   (which way of writing "empty" the schema uses; auto = what S2 found)

Answers are saved in results/calls.jsonl, so a run that hits a limit continues where it stopped.
Output: results/spike_report.md (paste it back into the chat) and results/spike_summary.json.
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import spike_client as sc                                  # noqa: E402
import spike_data as sd                                    # noqa: E402
import spike_fastpath as fp                                # noqa: E402
import spike_judge as sj                                   # noqa: E402
from spike_prompt import build_messages, prefix_for        # noqa: E402
from spike_schema import (SAFE_ORDER, STAGES, VARIANT_ORDER, response_format, schema_features, variant_model,   # noqa: E402
                          variant_response_format, variant_schema, variant_style)

BIG, SMALL = "openai/gpt-oss-120b", "openai/gpt-oss-20b"
ORDER = ["S1", "S2", "S3", "S4", "S5", "S6"]
TITLES = {
    "S1": "Does the strict format count as input tokens?",
    "S2": "Does strict mode accept our schema (shared definitions, nullable fields, 53 categories, Urdu)?",
    "S3": "How many thinking tokens does the model spend at the lowest setting?",
    "S4": "Is the smaller model good enough compared with the bigger one?",
    "S5": "Do repeated prompts get cached (cheaper)?",
    "S6": "How many messages could skip the AI completely?",
}
NEEDS_KEY = {"S1", "S2", "S3", "S4", "S5"}


class Ctx:
    def __init__(self, caller, args, mine, variant="sentinel", results_dir=None):
        self.caller, self.args, self.mine, self.variant, self.results_dir = caller, args, mine, variant, results_dir

    def messages(self, text, variant=None):
        return build_messages(text, variant_style(variant or self.variant))


def understand(ctx, model, item, effort="low", prefix="U"):
    """One 'understand' call on the chosen strict schema. Same request => the saved answer is reused across spikes."""
    key = f"{prefix}|{ctx.variant}|{model}|{effort}|{item['id']}"
    return ctx.caller.call(key, model, ctx.messages(item["text"]), response_format=variant_response_format(ctx.variant),
                           reasoning_effort=effort, max_completion_tokens=1500)


def _fits(variant, result):
    """200 and an answer that really fits the schema."""
    if result.status != 200:
        return False
    try:
        variant_model(variant).model_validate_json(result.content)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------------------------------
def spike_s1(ctx):
    msg = "Spent Rs 500 on chai today"
    tried, chosen = {}, None
    for name in SAFE_ORDER:                      # the first way of writing the schema that Groq accepts
        r = ctx.caller.call(f"S1|full|{name}", BIG, ctx.messages(msg, name), response_format=variant_response_format(name),
                            max_completion_tokens=300)
        tried[name] = r
        if r.status == 200:
            chosen = name
            break
    style_variant = chosen or SAFE_ORDER[0]
    out = {"full": tried.get(chosen) if chosen else None}
    for name, rf in (("none", None), ("trivial", response_format(STAGES[0][1], "trivial"))):
        out[name] = ctx.caller.call(f"S1|{name}|{style_variant}", BIG, ctx.messages(msg, style_variant), response_format=rf,
                                    max_completion_tokens=300)
    prompts = {k: (v.usage.prompt if v is not None and v.status == 200 else None) for k, v in out.items()}
    verdict = sj.s1_verdict(prompts["none"], prompts["trivial"], prompts["full"])
    return {
        "prompt_tokens": prompts, "variant_used": chosen,
        "schema_chars_full": schema_features(variant_schema(chosen))["chars"] if chosen else None, **verdict,
        "refused_variants": {k: v.error for k, v in tried.items() if v.status != 200},
        "errors": {k: v.error for k, v in out.items() if v is not None and v.status != 200},
        "verdict": "FAIL" if not verdict["known"] else ("COUNTED" if verdict["counts_as_input"] else "NOT COUNTED"),
    }


def spike_s2(ctx):
    stages = {}
    for name, model, what, msg in STAGES:
        r = ctx.caller.call(f"S2|{name}", BIG, ctx.messages(msg, "sentinel"), response_format=response_format(model, name),
                            max_completion_tokens=1500)
        ok = r.status == 200
        if ok:
            try:
                model.model_validate_json(r.content)
            except Exception:
                ok = False
        stages[name] = {"what": what, "ok": ok, "status": r.status, "finish_reason": r.finish_reason, "error": r.error}
    variants = {}
    for name in VARIANT_ORDER:
        r = ctx.caller.call(f"S2|full|{name}", BIG, ctx.messages("chai 50 aur samosa 100", name),
                            response_format=variant_response_format(name), max_completion_tokens=1500)
        variants[name] = {"ok": _fits(name, r), "status": r.status, "finish_reason": r.finish_reason, "error": r.error,
                          "features": schema_features(variant_schema(name))}
    accepted = [n for n in VARIANT_ORDER if variants[n]["ok"]]
    scores = []
    if accepted:
        ctx.variant = accepted[0]                # the Urdu check, and everything after it, uses the first accepted variant
        _remember_variant(ctx)
        for gid in sd.URDU_CHECK_IDS:
            item = sd.by_id()[gid]
            r = understand(ctx, BIG, item)
            scores.append(sj.score_message(item, r.content if r.status == 200 else None, ctx.variant))
    verdict = sj.s2_verdict({k: v["ok"] for k, v in stages.items()}, {k: v["ok"] for k, v in variants.items()}, scores)
    return {"stages": stages, "variants": variants, "urdu": scores, "variant_used_for_urdu": ctx.variant if accepted else None,
            **verdict}


def _remember_variant(ctx):
    if ctx.results_dir is not None:
        (Path(ctx.results_dir) / "variant.json").write_text(json.dumps({"variant": ctx.variant}), encoding="utf-8")


def resolve_variant(choice, results_dir):
    """--variant wins; "auto" reads what S2 found last time; otherwise the recommended default."""
    if choice != "auto":
        return choice
    try:
        saved = json.loads((Path(results_dir) / "variant.json").read_text(encoding="utf-8")).get("variant")
        return saved if saved in VARIANT_ORDER else SAFE_ORDER[0]
    except (OSError, ValueError):
        return SAFE_ORDER[0]


def spike_s3(ctx):
    sample = sd.stratified(sd.GOLD, 6)
    rows = {"low": [], "medium": []}
    for effort in rows:
        for item in sample:
            r = understand(ctx, BIG, item, effort)
            if r.status == 200:
                rows[effort].append(r)
    summary = {}
    for effort, results in rows.items():
        completions = [r.usage.completion for r in results]
        reported = [r.usage.reasoning for r in results if r.usage.reasoning is not None]
        est = [round(r.usage.reasoning_chars / 4) for r in results]
        summary[effort] = {
            "calls": len(results),
            "completion_tokens_avg": round(sum(completions) / len(completions)) if completions else None,
            "completion_tokens_max": max(completions) if completions else None,
            "reasoning_tokens_reported_avg": round(sum(reported) / len(reported)) if reported else None,
            "reasoning_tokens_estimated_avg": round(sum(est) / len(est)) if est else None,
            "reasoning_reported_by_api": bool(reported),
            "truncated_calls": sum(1 for r in results if r.finish_reason == "length"),
        }
    low = [r.usage.completion for r in rows["low"]]
    summary["recommended_max_completion_tokens"] = sj.recommended_token_cap(low)
    summary["variant"] = ctx.variant
    summary["verdict"] = "NO DATA" if not rows["low"] else ("TRUNCATION" if summary["low"]["truncated_calls"] else "OK")
    return summary


def spike_s4(ctx):
    sample = sd.stratified(sd.GOLD, ctx.args.n)
    models = [m.strip() for m in ctx.args.models.split(",") if m.strip()]
    out = {"messages_used": len(sample), "variant": ctx.variant, "models": {}}
    for model in models:
        scores, lat, tot, failed = [], [], [], 0
        for item in sample:
            r = understand(ctx, model, item)
            if r.status != 200:
                failed += 1
                scores.append(sj.score_message(item, None, ctx.variant))
                continue
            scores.append(sj.score_message(item, r.content, ctx.variant))
            lat.append(r.latency_s)
            tot.append(r.usage.total)
        summary = sj.summarize_scores(scores, lat, tot)
        summary["calls_failed"] = failed
        summary["misses"] = [
            {"id": s["id"], "intent_ok": s["intent_ok"], "valid": s["valid"],
             "category_ok": all(i["slug_ok"] for i in s["items"])}
            for s in scores if not (s["valid"] and s["intent_ok"] and all(i["slug_ok"] for i in s["items"]))
        ]
        out["models"][model] = summary
    big, small = out["models"].get(BIG), out["models"].get(SMALL)
    out["verdict"] = "COMPARED" if big and small else "ONE MODEL"
    if big and small and None not in (big["intent_pct"], small["intent_pct"], big["category_top1_pct"], small["category_top1_pct"]):
        out["smaller_model_gap"] = {
            "intent_points": round(big["intent_pct"] - small["intent_pct"], 1),
            "category_points": round(big["category_top1_pct"] - small["category_top1_pct"], 1),
        }
    return out


def spike_s5(ctx):
    sample = sd.stratified(sd.GOLD, 6)
    rows = []
    for item in sample:
        r = ctx.caller.call(f"S5|{ctx.variant}|{item['id']}", BIG, ctx.messages(item["text"]),
                            response_format=variant_response_format(ctx.variant), max_completion_tokens=1500)
        if r.status == 200:
            rows.append({"id": item["id"], "prompt_tokens": r.usage.prompt, "cached_tokens": r.usage.cached,
                         "hit_pct": sj._rate(r.usage.cached, r.usage.prompt)})
    later = rows[1:]
    hits = sum(1 for r in later if r["cached_tokens"] > 0)
    return {
        "calls": rows, "later_calls_with_a_hit": hits, "later_calls": len(later),
        "static_prefix_chars": len(prefix_for(variant_style(ctx.variant))), "variant": ctx.variant,
        "verdict": "NO DATA" if len(rows) < 2 else ("CACHED" if hits >= max(1, len(later) - 1) else "NOT CACHED"),
    }


def spike_s6(ctx):
    result = fp.evaluate(sd.GOLD, ctx.mine)
    share = sj._rate(result["gold_handled"], result["gold_total"])
    result["gold_share_handled_pct"] = share
    result["gold_correct_of_handled_pct"] = sj._rate(result["gold_correct"], result["gold_handled"])
    result["my_share_handled_pct"] = sj._rate(result["my_handled"], result["my_total"])
    result["verdict"] = "MEASURED"
    return result


SPIKES = {"S1": spike_s1, "S2": spike_s2, "S3": spike_s3, "S4": spike_s4, "S5": spike_s5, "S6": spike_s6}


# --------------------------------------------------------------------------------------------------
def plan(args):
    n4 = min(args.n, len(sd.GOLD)) * len([m for m in args.models.split(",") if m.strip()])
    calls = {"S1": len(SAFE_ORDER) + 2, "S2": len(STAGES) + len(VARIANT_ORDER) + len(sd.URDU_CHECK_IDS), "S3": 12, "S4": n4, "S5": 6, "S6": 0}
    return calls


def report_markdown(summary: dict, meta: dict) -> str:
    L = ["# Step 4a spike report", "",
         f"Calls sent now: {meta['calls_made']}  |  reused from earlier runs: {meta['reused']}  |  tokens counted against this run's budget: {meta['budget_used']} of {meta['budget_limit']}", ""]
    for name in ORDER:
        if name not in summary:
            continue
        s = summary[name]
        L += [f"## {name}: {TITLES[name]}", "", f"**Verdict: {s.get('verdict')}**", ""]
        if name == "S1":
            L += [f"- schema form that Groq accepted: {s['variant_used']}",
                  f"- prompt tokens without a schema / with a short schema / with ours: {s['prompt_tokens']}",
                  f"- extra tokens caused by our full schema: {s['delta_full']} (schema is {s['schema_chars_full']} characters)",
                  f"- extra tokens caused by the short schema: {s['delta_trivial']}"]
            L += [f"- refused ({k}): {v}" for k, v in s["refused_variants"].items()]
            L += [f"- error ({k}): {v}" for k, v in s["errors"].items()]
        elif name == "S2":
            L.append("Basic features:")
            for k, v in s["stages"].items():
                L.append(f"- {k} ({v['what']}): {'ok' if v['ok'] else 'REFUSED'} (HTTP {v['status']}){'' if v['ok'] else ' ' + str(v['error'])}")
            L.append("")
            L.append("Ways of writing the full schema:")
            for k, v in s["variants"].items():
                f = v["features"]
                L.append(f"- {k} ({f['chars']} characters, anyOf: {f['has_anyof']}, type arrays: {f['has_type_array']}): "
                         f"{'ACCEPTED' if v['ok'] else 'REFUSED'} (HTTP {v['status']}){'' if v['ok'] else ' ' + str(v['error'])}")
            L += ["", f"- recommended form: {s['recommended_variant']}",
                  f"- Urdu and Roman Urdu answers that were not valid: {s['invalid_urdu_answers']}",
                  f"- amount copied exactly from the message (Urdu set): {s['urdu_amount_copied_pct']}%"]
        elif name == "S3":
            for effort in ("low", "medium"):
                e = s[effort]
                L.append(f"- {effort}: {e['calls']} calls, answer tokens avg {e['completion_tokens_avg']} / max {e['completion_tokens_max']}, "
                         f"thinking tokens avg {e['reasoning_tokens_reported_avg'] if e['reasoning_reported_by_api'] else str(e['reasoning_tokens_estimated_avg']) + ' (estimated from text, the API did not report them)'}, "
                         f"cut off: {e['truncated_calls']}")
            L.append(f"- suggested answer-length cap: {s['recommended_max_completion_tokens']} tokens")
        elif name == "S4":
            L += ["", "| model | valid | intent | amount copied | amount value | category exact | category group | p50 s | p95 s | tokens/call |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
            for model, m in s["models"].items():
                L.append(f"| {model} | {m['valid_json_pct']} | {m['intent_pct']} | {m['amount_copied_pct']} | {m['amount_value_pct']} | "
                         f"{m['category_top1_pct']} | {m['category_group_pct']} | {m['latency_p50_s']} | {m['latency_p95_s']} | {m['tokens_per_call_avg']} |")
            L += ["", f"- amounts the strict money parser cannot read (words, 5k, lakh): {[(k, v['amounts_parser_rejects']) for k, v in s['models'].items()]}"]
            if "smaller_model_gap" in s:
                L.append(f"- the bigger model is ahead by: {s['smaller_model_gap']} percentage points")
            for model, m in s["models"].items():
                L.append(f"- {model} missed: {[x['id'] for x in m['misses']]}")
        elif name == "S5":
            for r in s["calls"]:
                L.append(f"- {r['id']}: {r['cached_tokens']} of {r['prompt_tokens']} prompt tokens cached ({r['hit_pct']}%)")
            L.append(f"- later calls with a cache hit: {s['later_calls_with_a_hit']} of {s['later_calls']}")
        elif name == "S6":
            L += [f"- test set: {s['gold_handled']} of {s['gold_total']} handled without the AI ({s['gold_share_handled_pct']}%), "
                  f"{s['gold_correct']} of those read correctly",
                  f"- wrong: {s['wrong']}"]
            if s["my_total"]:
                L.append(f"- YOUR messages: {s['my_handled']} of {s['my_total']} handled without the AI ({s['my_share_handled_pct']}%)")
        L.append("")
    return "\n".join(L)


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Step 4a AI spikes")
    ap.add_argument("--only", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--budget", type=int, default=sc.DEFAULT_BUDGET_TOKENS)
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--models", default=f"{BIG},{SMALL}")
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--results-dir", default=str(HERE / "results"))
    ap.add_argument("--my-messages", default="")
    ap.add_argument("--continue-after-stop", action="store_true")
    ap.add_argument("--variant", default="auto", choices=["auto", *SAFE_ORDER])
    args = ap.parse_args(argv)

    wanted = [s.strip().upper() for s in args.only.split(",") if s.strip()] or list(ORDER)
    unknown = [s for s in wanted if s not in SPIKES]
    if unknown:
        print(f"unknown spike(s): {unknown}; choose from {ORDER}")
        return 1
    wanted = sorted(set(wanted), key=ORDER.index)

    if args.dry_run:
        calls = plan(args)
        print("DRY RUN: nothing is sent, no key is read.\n")
        for name in wanted:
            worst = calls[name] * sc.DEFAULT_ESTIMATE_TOKENS
            print(f"  {name}: {TITLES[name]}\n      up to {calls[name]} calls, at most about {worst} tokens (cached tokens are not counted by Groq)")
        total = sum(calls[n] for n in wanted)
        print(f"\n  total up to {total} calls; budget guard {args.budget} tokens; "
              f"free limits: {sc.FREE_RPM} calls/min, 8,000 tokens/min, 200,000 tokens/day (shared with the live chatbot)")
        print("  the runner slows itself down to stay inside those limits, so the full run takes roughly 15 to 30 minutes")
        return 0

    mine = []
    if args.my_messages:
        mine = [ln.strip() for ln in Path(args.my_messages).read_text(encoding="utf-8").splitlines() if ln.strip()]

    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    calls_file = results_dir / "calls.jsonl"
    if args.fresh and calls_file.exists():
        calls_file.unlink()
    store = sc.Store(calls_file)
    budget = sc.Budget(args.budget)

    caller = None
    if NEEDS_KEY & set(wanted):
        try:
            caller = sc.GroqCaller(sc.load_key(), budget, store)
        except sc.MissingKey as exc:
            print(f"{exc}\n(S6 needs no key: run with --only S6)")
            return 1
    ctx = Ctx(caller, args, mine, resolve_variant(args.variant, results_dir), results_dir)

    summary, code = {}, 0
    for name in wanted:
        print(f"\n== {name}: {TITLES[name]}")
        try:
            summary[name] = SPIKES[name](ctx)
        except sc.BudgetExceeded as exc:
            print(f"   stopped: {exc}\n   Answers so far are saved; run again later (tomorrow, or with --budget higher) to continue.")
            code = 3
            break
        print(f"   verdict: {summary[name].get('verdict')}")
        if name in ("S1", "S2") and summary[name].get("verdict") in ("STOP", "FAIL") and not args.continue_after_stop:
            print("   STOP: this decides whether the plan works. Send me results/spike_report.md and I will bring options.")
            code = 2
            break

    meta = {"calls_made": caller.calls_made if caller else 0, "reused": len(store.records) - (caller.calls_made if caller else 0),
            "budget_used": budget.used, "budget_limit": budget.limit}
    (results_dir / "spike_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (results_dir / "spike_report.md").write_text(report_markdown(summary, meta), encoding="utf-8")
    print(f"\nReport: {results_dir / 'spike_report.md'}  (paste it back into the chat)")
    return code


if __name__ == "__main__":
    sys.exit(main())
