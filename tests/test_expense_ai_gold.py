"""
The gold set, offline half (Path step 5a): every message's IDEAL model answer is replayed through the real code that follows
the model, and the result must equal the expectation. 100% is required here because no model is involved: a miss is a bug in a
normaliser, the cross-check, the period reader or the edit resolver.

The live half (the real model reading these same messages) is tests/test_expense_ai_gold_live.py (marker `live`) and
scripts/score_gold_live.py. The replay code is in tests/gold_runner.py.
"""
from collections import Counter
from datetime import datetime

import pytest

from tests import gold_set as g
from app.services.expense_edit import build_edit
from app.services.expense_understand import to_domain
from tests.gold_runner import build_world, comparable, evaluate


@pytest.fixture
def world(db_session, make_user, clock):
    return build_world(db_session, make_user())


# ── the set itself ─────────────────────────────────────────────────────────────────────────────

def test_the_gold_set_has_about_150_messages_with_unique_ids_covering_every_language_and_kind():
    ids = [c.id for c in g.GOLD]
    assert len(ids) == len(set(ids)) and 140 <= len(ids) <= 170
    by_lang = Counter(c.lang for c in g.GOLD)
    for lang in ("en", "ur", "roman_ur", "mixed", "multi", "words", "adversarial"):
        assert by_lang[lang] >= 7, lang
    by_intent = Counter(c.intent for c in g.GOLD)
    assert by_intent["expense_log"] >= 90 and by_intent["expense_query"] >= 20 and by_intent["expense_edit_delete"] >= 15
    assert by_intent["islamic_finance_qa"] + by_intent["off_topic"] >= 10


def test_every_log_message_contains_the_amount_the_model_is_expected_to_copy():
    """A gold message the model could not possibly read would make the live score meaningless."""
    from app.services.expense_normalise import amount_in_message
    for case in g.GOLD:
        if case.expect["outcome"] == "card":
            for item in case.model["items"]:
                assert amount_in_message(item["amount_text"], case.message), case.id


def test_the_seed_is_what_the_cases_assume():
    assert len(g.SEED) == 11 and sum(r for r, *_ in g.SEED) == 39940


# ── the replay ─────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("case", g.GOLD, ids=[c.id for c in g.GOLD])
def test_gold_case_ends_as_expected(world, case):
    want, got = comparable(case, evaluate(world, case))
    assert got == want


def test_every_query_total_equals_an_independent_python_sum_over_the_seed(world):
    """The oracle here is plain Python over the SEED list, not SQL: a wrong window or a wrong filter in the real path shows up."""
    checked = 0
    for case in (c for c in g.GOLD if c.expect["outcome"] == "answer"):
        got = evaluate(world, case)
        result = got["result"]
        start, end = (datetime.fromisoformat(x).date() for x in case.expect["window"])
        slug = case.expect["category"]
        hits = [r for r, s, d, _ in g.SEED if start <= d < end and (slug is None or s == slug or s.startswith(slug + "."))]
        assert result.total_paisa == sum(hits) * 100 and result.count == len(hits), case.id
        if result.groups:
            assert sum(x.total_paisa for x in result.groups) == result.total_paisa and sum(x.count for x in result.groups) == result.count
        checked += 1
    assert checked >= 20


def test_every_edit_case_names_the_entry_the_person_meant(world):
    db, uid, rows, _ = world
    wanted = {"ed01": 300000, "ed02": 1500000, "ed03": 64000, "ed06": 850000, "ed09": 200000, "ed10": 750000, "ed11": 64000, "ed13": 750000}
    for case in (c for c in g.GOLD if c.id in wanted):
        out = build_edit(db, uid, to_domain(world[3].model_validate(case.model)), case.message, "en", g.TODAY, rows)
        assert [c.amount_paisa for c in out.payload.candidates] == [wanted[case.id]], case.id


def test_adversarial_cases_never_make_a_card_for_someone_else_or_with_a_changed_amount(world):
    for case in (c for c in g.GOLD if c.lang == "adversarial"):
        got = evaluate(world, case)
        if got["outcome"] == "card":                                  # only the injection case: a card for ME with MY words' amount
            assert case.id == "ad11" and got["items"][0][0] == 99999900
        else:
            assert got["outcome"] == "refused", case.id


# ── the live scorer's own arithmetic ───────────────────────────────────────────────────────────

def _verdicts(world, mutate=None):
    out = []
    for case in g.GOLD:
        form = case.model if mutate is None else mutate(case)
        got = evaluate(world, case) if form is case.model else evaluate(world, g.Case(**{**case.__dict__, "model": form}))
        out.append(g.score(case, form, {k: v for k, v in got.items() if k != "result"}))
    return out


def test_an_ideal_model_scores_one_hundred_percent_on_every_number(world):
    summary = g.summarise(_verdicts(world))
    for key, row in summary.items():
        assert row["ok"] == row["scored"] and row["pass"] and row["percent"] == "100.0", key
    assert summary["intent"]["scored"] == len(g.GOLD) and summary["amount"]["scored"] >= 100


def test_a_model_that_misreads_amounts_or_categories_fails_the_thresholds(world):
    def wrong_category(case):
        form = {**case.model, "items": [dict(i, category_slug="other.other") for i in case.model["items"]]}
        return form if case.model["items"] else case.model
    summary = g.summarise(_verdicts(world, wrong_category))
    assert not summary["subcategory"]["pass"] and summary["intent"]["pass"]

    def wrong_intent(case):
        return {**case.model, "intent": "off_topic"} if case.id.startswith(("en0", "ru0")) else case.model
    assert not g.summarise(_verdicts(world, wrong_intent))["intent"]["pass"]


def test_a_changed_amount_that_slips_through_is_a_failure_but_a_refused_one_is_not(world):
    case = next(c for c in g.GOLD if c.id == "en03")
    wrong = {"outcome": "card", "items": [(300001, "transport.fuel", g.TODAY_ISO, "")]}
    assert g.score(case, case.model, wrong)["amount"] is False
    assert g.score(case, case.model, {"outcome": "refused", "problem": "amount"})["amount"] is True
    assert g.score(case, None, {})["intent"] is False


def test_summarise_uses_integer_arithmetic_at_the_boundary():
    ok = [{"intent": True, "amount": True, "date": True, "subcategory": True}] * 199
    one_wrong = ok + [{"intent": False, "amount": False, "date": False, "subcategory": False}]
    row = g.summarise(one_wrong)["amount"]                           # 199 of 200 = 99.5%: exactly the threshold, so it passes
    assert row["percent"] == "99.5" and row["pass"] is True
    two_wrong = ok[:198] + [{"intent": False, "amount": False, "date": False, "subcategory": False}] * 2
    assert g.summarise(two_wrong)["amount"]["pass"] is False
