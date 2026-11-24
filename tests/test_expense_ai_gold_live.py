"""
The gold set against the REAL model (Path step 5a). Marker `live`: never runs by default.

    $env:GROQ_API_KEY = "..."
    python -m pytest -q -m live tests/test_expense_ai_gold_live.py

A first small slice only: the full run (about 155 calls, paced for Groq's free limits) is scripts/score_gold_live.py, which prints
and saves the four numbers. This test fails if the slice misses the thresholds, so it is a quick smoke check, not the scorer.
"""
import asyncio
import os
import time

import httpx
import pytest

from app.services import expense_understand
from app.services.expense_understand import understand
from tests import gold_set as g
from tests.gold_runner import build_world, evaluate_understood

pytestmark = pytest.mark.live
SLICE = ["en01", "en04", "en11", "ru02", "ru09", "ur02", "ur13", "mu02", "wd02", "wd07", "qu01", "qu10", "ed01", "ed07", "ot02"]


@pytest.mark.skipif(not os.environ.get("GROQ_API_KEY"), reason="GROQ_API_KEY is not set")
def test_a_small_slice_of_the_gold_set_meets_the_thresholds(db_session, make_user, clock, monkeypatch):
    monkeypatch.setattr(expense_understand, "_client", lambda: httpx.AsyncClient(timeout=40))   # undo the offline guard
    world = build_world(db_session, make_user())
    verdicts = []
    for case in (c for c in g.GOLD if c.id in SLICE):
        result = asyncio.run(understand(db_session, case.message))
        outcome = {} if result.understood is None else {
            k: v for k, v in evaluate_understood(world, result.understood, case.message).items() if k != "result"}
        form = None if result.understood is None else result.understood.model_dump(mode="json")
        verdicts.append(g.score(case, form, outcome))
        time.sleep(3)
    summary = g.summarise(verdicts)
    assert summary["intent"]["pass"] and summary["amount"]["pass"], summary
