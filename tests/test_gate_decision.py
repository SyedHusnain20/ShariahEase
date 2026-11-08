"""Decision rules for C1 and Gate G (hand-off section 9): every threshold at its exact boundary, integer arithmetic only."""
import ast
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "gate_decision.py"
SPEC = importlib.util.spec_from_file_location("gate_decision_t", SCRIPT)
gd = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gd)


def counts(strict=False, **kw):
    """All-zero counts with overrides. Unless strict, the funnel is kept valid (each stage at least as big as the next)."""
    base = dict(starters=0, starters_judgeable=0, activated=0, retention_base=0, retained=0, active_users_14d=0,
                ai_users_14d=0, ai_entries_eligible=0, ai_entries_corrected=0, upgrade_interest_users=0)
    base.update(kw)
    if not strict:
        base["activated"] = max(base["activated"], base["retention_base"])
        base["starters_judgeable"] = max(base["starters_judgeable"], base["activated"])
        base["starters"] = max(base["starters"], base["starters_judgeable"])
        base["active_users_14d"] = max(base["active_users_14d"], base["ai_users_14d"])
        base["ai_entries_eligible"] = max(base["ai_entries_eligible"], base["ai_entries_corrected"])
    return base


GREEN = dict(retention_base=100, retained=50, active_users_14d=100, ai_users_14d=50, ai_entries_eligible=100,
             ai_entries_corrected=10, upgrade_interest_users=20)


def green_all(**kw):
    """Counts where every metric is green; override any of them (the funnel is rebuilt to stay valid)."""
    return counts(**{**GREEN, **kw})


# ── helpers ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("n, d, text", [(0, 0, "n/a"), (5, 0, "n/a"), (0, 10, "0.0%"), (1, 3, "33.3%"), (2, 3, "66.6%"),
                                         (30, 100, "30.0%"), (299, 1000, "29.9%"), (1, 1, "100.0%"), (7, 2000, "0.3%")])
def test_pct(n, d, text):
    assert gd.pct(n, d) == text


def test_no_floats_and_no_division_in_the_decision_code():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Div)], "true division found"
    assert not [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, float)], "float constant found"


# ── C1 ───────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("starters, judge, act, proceed", [
    (30, 10, 3, True),            # exactly 30 starters and exactly 30 %
    (29, 10, 3, False),           # one starter short
    (30, 10, 2, False),           # 20 %
    (1000, 1000, 299, False), (1000, 1000, 300, True),
    (30, 7, 2, False),            # 28.5 %
    (30, 3, 1, True),             # 33.3 %
    (500, 0, 0, False),           # nobody old enough to judge
    (0, 0, 0, False),
    (30, 30, 30, True),
])
def test_c1(starters, judge, act, proceed):
    d = gd.decide_c1(counts(starters=starters, starters_judgeable=judge, activated=act))
    assert d["proceed"] is proceed
    assert d["reasons"]


def test_c1_reasons_say_what_is_missing():
    d = gd.decide_c1(counts(starters=12, starters_judgeable=5, activated=1))
    assert d["proceed"] is False
    assert any("12" in r and "30" in r for r in d["reasons"]) and any("20.0%" in r for r in d["reasons"])
    d = gd.decide_c1(counts(starters=40, starters_judgeable=0))
    assert any("7 days old" in r for r in d["reasons"])


# ── Gate G: each metric at its boundaries ────────────────────────────────────────

def status(metric, **kw):
    return gd.decide_gate(green_all(**kw))["metrics"][metric]["status"]


@pytest.mark.parametrize("retained, base, expected", [
    (60, 200, "green"), (59, 200, "amber"),               # 30 %
    (30, 200, "amber"), (29, 200, "red"),                 # 15 %
    (9, 30, "green"), (8, 30, "amber"), (5, 30, "amber"), (4, 30, "red"),
    (10, 29, "inconclusive"), (0, 0, "inconclusive"), (29, 29, "inconclusive"),
])
def test_retention_thresholds(retained, base, expected):
    assert status("week4_retention", retained=retained, retention_base=base) == expected


@pytest.mark.parametrize("ai, active, expected", [
    (5, 20, "green"), (4, 20, "amber"),                   # 25 %
    (2, 20, "amber"), (1, 20, "red"),                     # 10 %
    (25, 100, "green"), (24, 100, "amber"), (10, 100, "amber"), (9, 100, "red"),
    (20, 19, "inconclusive"), (0, 0, "inconclusive"),
])
def test_ai_adoption_thresholds(ai, active, expected):
    ai = min(ai, active)
    assert status("ai_adoption", ai_users_14d=ai, active_users_14d=active) == expected


@pytest.mark.parametrize("corrected, eligible, expected", [
    (25, 100, "green"), (26, 100, "amber"),               # 25 %
    (40, 100, "amber"), (41, 100, "red"),                 # 40 %
    (7, 30, "green"), (8, 30, "amber"), (12, 30, "amber"), (13, 30, "red"),
    (29, 29, "not_scored"), (0, 0, "not_scored"), (0, 100, "green"), (100, 100, "red"),
])
def test_ai_correction_thresholds_lower_is_better(corrected, eligible, expected):
    assert status("ai_correction", ai_entries_corrected=corrected, ai_entries_eligible=eligible) == expected


@pytest.mark.parametrize("users, expected", [(10, "green"), (9, "amber"), (0, "amber"), (500, "green")])
def test_upgrade_interest_thresholds_and_never_red(users, expected):
    assert status("upgrade_interest", upgrade_interest_users=users) == expected


# ── the verdict ──────────────────────────────────────────────────────────────────

def verdict(**kw):
    return gd.decide_gate(green_all(**kw))["verdict"]


def test_all_green_is_green():
    assert verdict() == "green"


def test_one_amber_makes_amber_and_red_beats_amber():
    assert verdict(retained=40) == "green"                                    # 40 % of 100 is still green
    assert verdict(retained=20, retention_base=100) == "amber"                # 20 %
    assert verdict(ai_entries_corrected=30) == "amber"                        # 30 % of 100 AI entries corrected
    assert verdict(retained=20, retention_base=100, ai_users_14d=5) == "red"  # amber retention, but adoption 5 % is red
    assert verdict(ai_entries_corrected=50) == "red"


def test_upgrade_interest_alone_can_only_make_amber():
    assert verdict(upgrade_interest_users=0) == "amber"


def test_a_missing_sample_makes_it_inconclusive_even_with_a_red_metric():
    assert verdict(retention_base=10, retained=0, ai_users_14d=1) == "inconclusive"
    assert verdict(active_users_14d=19, ai_users_14d=0) == "inconclusive"


def test_too_few_ai_entries_is_not_scored_and_does_not_block():
    d = gd.decide_gate(green_all(ai_entries_eligible=10, ai_entries_corrected=10))
    assert d["metrics"]["ai_correction"]["status"] == "not_scored"
    assert d["verdict"] == "green"
    assert any("not scored" in r for r in d["reasons"])


def test_reasons_name_the_metrics_that_are_not_green():
    d = gd.decide_gate(green_all(retained=20, retention_base=100, upgrade_interest_users=3))
    assert "week4_retention is amber" in d["reasons"] and "upgrade_interest is amber" in d["reasons"]


# ── bad input stops the report ───────────────────────────────────────────────────

@pytest.mark.parametrize("change", [
    {"starters": -1}, {"starters": 1.5}, {"starters": "3"}, {"starters": True}, {"starters": None},
    {"starters": 1, "starters_judgeable": 2}, {"starters_judgeable": 1, "activated": 2}, {"activated": 1, "retention_base": 2},
    {"retention_base": 1, "retained": 2}, {"active_users_14d": 1, "ai_users_14d": 2},
    {"ai_entries_eligible": 1, "ai_entries_corrected": 2},
])
def test_inconsistent_or_malformed_counts_raise(change):
    with pytest.raises(ValueError):
        gd.decide_gate(counts(strict=True, **change))
    with pytest.raises(ValueError):
        gd.decide_c1(counts(strict=True, **change))


def test_a_missing_key_raises():
    m = counts()
    del m["retained"]
    with pytest.raises(ValueError):
        gd.decide_gate(m)
