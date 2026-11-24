"""
Path step 5a: score the REAL model on the gold set (tests/gold_set.py). Run it yourself, on your machine, with your own key:

    $env:GROQ_API_KEY = "..."                       # PowerShell; never paste the key into a chat
    python scripts/score_gold_live.py --limit 20    # a small first run
    python scripts/score_gold_live.py               # all of it (about 155 calls)

It calls the same `understand()` that the chat uses (so the same prompt, schema and provider chain), replays the model's answer
through the same code that follows it in the chat, and prints four numbers against the acceptance thresholds:
intent >= 97%, amount correct-or-refused >= 99.5%, date >= 97%, sub-category top-1 >= 85%.

Groq's free plan is 30 requests a minute, 8,000 tokens a minute and 200,000 tokens a day for this model, and the daily total is
shared with the website chatbot. The script therefore waits between calls (--pause, default 3 seconds), waits and retries when
the provider says "too many requests" (up to 3 times, 25 seconds apart), and can be run in parts (--slice 0:50, --ids en01,ru02,
--lang ur). Messages are synthetic (not real user data). Results are written to scripts/spikes/results/ (git-ignored).
Nothing is written to any real database: the entries the query and edit cases look at live in a throw-away in-memory database.
"""
import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine                       # noqa: E402
from sqlalchemy.orm import sessionmaker                     # noqa: E402
from sqlalchemy.pool import StaticPool                      # noqa: E402

from app.database.db import enable_sqlite_foreign_keys     # noqa: E402
from app.database.migrate import upgrade_to_head           # noqa: E402
from app.database.models import User                        # noqa: E402
from app.services.expense_understand import understand      # noqa: E402
from tests import gold_set as g                             # noqa: E402
from tests.gold_runner import build_world, evaluate_understood  # noqa: E402


def select(args):
    cases = list(g.GOLD)
    if args.ids:
        wanted = set(args.ids.split(","))
        cases = [c for c in cases if c.id in wanted]
    if args.lang:
        cases = [c for c in cases if c.lang == args.lang]
    if args.slice:
        a, _, b = args.slice.partition(":")
        cases = cases[int(a or 0): int(b) if b else None]
    if args.limit:
        cases = cases[: args.limit]
    return cases


async def read_one(db, message, retries=3, wait=25):
    for attempt in range(retries + 1):
        result = await understand(db, message)
        if result.understood is not None or attempt == retries:
            return result
        time.sleep(wait)                                    # "too many requests" or a blip: wait and try again


async def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--slice", default="")
    ap.add_argument("--ids", default="")
    ap.add_argument("--lang", default="")
    ap.add_argument("--pause", type=float, default=3.0)
    args = ap.parse_args(argv)

    import os
    if not os.environ.get("GROQ_API_KEY"):
        print("GROQ_API_KEY is not set. Set it in this PowerShell window first (do not paste it anywhere else).")
        return 2
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    enable_sqlite_foreign_keys(engine)
    upgrade_to_head(engine)
    db = sessionmaker(bind=engine)()
    user = User(name="Gold", email=f"gold{uuid.uuid4().hex[:6]}@example.com", hashed_password="x")
    db.add(user)
    db.commit()
    world = build_world(db, user.id)

    cases, verdicts, rows, tokens = select(args), [], [], 0
    for n, case in enumerate(cases, start=1):
        result = await read_one(db, case.message)
        tokens += result.tokens
        if result.understood is None:
            outcome, form = {"outcome": "no_answer", "problem": result.problem}, None
        else:
            outcome = {k: v for k, v in evaluate_understood(world, result.understood, case.message).items() if k != "result"}
            form = result.understood.model_dump(mode="json")
        verdict = g.score(case, form, outcome)
        verdicts.append(verdict)
        bad = [k for k, v in verdict.items() if v is False]
        rows.append({"id": case.id, "intent_wanted": case.intent, "outcome": outcome, "verdict": verdict})
        print(f"{n:>3}/{len(cases)} {case.id:<5} {'ok' if not bad else 'MISS ' + ','.join(bad)}", flush=True)
        time.sleep(args.pause)

    summary = g.summarise(verdicts)
    print("\nResult (ok / scored, percent, need):")
    for key, row in summary.items():
        print(f"  {key:<12} {row['ok']:>3} / {row['scored']:<3} {row['percent']:>6}%  need {row['need']}%  {'PASS' if row['pass'] else 'FAIL'}")
    print(f"  tokens charged by the provider: {tokens}")
    out = ROOT / "scripts" / "spikes" / "results"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"gold_live_{time.strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps({"summary": summary, "cases": rows, "tokens": tokens}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  saved: {path}")
    return 0 if all(r["pass"] for r in summary.values()) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
