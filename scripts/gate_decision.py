"""
Decision rules for checkpoint C1 and Gate G (hand-off section 9). Pure functions: they take the counts that
gate_metrics.sql produced and return a verdict. Every comparison is integer arithmetic (a/b >= p% is a*100 >= p*b):
no floats, so a threshold is never decided by rounding.

C1 (go / no-go before building the AI steps):
    proceed only if  starters >= 30  AND  activated >= 30% of the starters old enough to judge (first expense 7+ days old).

Gate G (after the AI steps have run for the measurement period), one row per metric:

    metric                 green        amber          red          minimum sample (below it: inconclusive)
    week-4 retention       >= 30 %      15 to < 30 %   < 15 %       30 activated starters
    AI adoption (14 days)  >= 25 %      10 to < 25 %   < 10 %       20 active users
    AI correction (24 h)   <= 25 %      > 25 to 40 %   > 40 %       30 AI entries (below it: not scored, does not block)
    upgrade interest       >= 10 users  < 10 users     (never red alone)

    Verdict: inconclusive if retention or adoption is below its minimum sample; otherwise red if any scored metric is red,
    amber if any is amber, green only if every scored metric is green.
"""

C1_MIN_STARTERS = 30
C1_MIN_ACTIVATED_PERCENT = 30

RETENTION_MIN_SAMPLE, RETENTION_GREEN, RETENTION_AMBER = 30, 30, 15
ADOPTION_MIN_SAMPLE, ADOPTION_GREEN, ADOPTION_AMBER = 20, 25, 10
CORRECTION_MIN_SAMPLE, CORRECTION_GREEN, CORRECTION_AMBER = 30, 25, 40      # lower is better
UPGRADE_GREEN_USERS = 10

COUNT_KEYS = (
    "starters", "starters_judgeable", "activated", "retention_base", "retained",
    "active_users_14d", "ai_users_14d", "ai_entries_eligible", "ai_entries_corrected", "upgrade_interest_users",
)


def pct(num: int, den: int) -> str:
    """'33.3%' with one decimal, from integers only (rounded down); 'n/a' when there is nothing to divide by."""
    if den <= 0:
        return "n/a"
    tenths = (num * 1000) // den
    return f"{tenths // 10}.{tenths % 10}%"


def check_counts(m: dict) -> None:
    """The counts must make sense; a SQL slip should stop the report, not produce a verdict."""
    for key in COUNT_KEYS:
        v = m.get(key)
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise ValueError(f"count {key!r} must be a non-negative whole number, got {v!r}")
    chains = [("starters_judgeable", "starters"), ("activated", "starters_judgeable"), ("retention_base", "activated"),
              ("retained", "retention_base"), ("ai_users_14d", "active_users_14d"), ("ai_entries_corrected", "ai_entries_eligible")]
    for small, big in chains:
        if m[small] > m[big]:
            raise ValueError(f"inconsistent counts: {small} ({m[small]}) is larger than {big} ({m[big]})")


def decide_c1(m: dict) -> dict:
    check_counts(m)
    starters, judge, act = m["starters"], m["starters_judgeable"], m["activated"]
    reasons = []
    enough_starters = starters >= C1_MIN_STARTERS
    if not enough_starters:
        reasons.append(f"only {starters} users have started tracking; at least {C1_MIN_STARTERS} are needed")
    if judge == 0:
        activated_ok = False
        reasons.append("no starter is 7 days old yet, so activation cannot be judged")
    else:
        activated_ok = act * 100 >= C1_MIN_ACTIVATED_PERCENT * judge
        if not activated_ok:
            reasons.append(f"activated is {pct(act, judge)} of {judge} judgeable starters; at least {C1_MIN_ACTIVATED_PERCENT}% is needed")
    proceed = enough_starters and activated_ok
    return {
        "proceed": proceed,
        "starters": starters,
        "judgeable_starters": judge,
        "activated": act,
        "activated_percent": pct(act, judge),
        "reasons": reasons if not proceed else ["both conditions are met"],
    }


def _higher_is_better(num: int, den: int, min_sample: int, green: int, amber: int) -> dict:
    out = {"numerator": num, "denominator": den, "percent": pct(num, den), "min_sample": min_sample,
           "green_at": f">= {green}%", "amber_at": f">= {amber}%"}
    if den < min_sample:
        out["status"] = "inconclusive"
    elif num * 100 >= green * den:
        out["status"] = "green"
    elif num * 100 >= amber * den:
        out["status"] = "amber"
    else:
        out["status"] = "red"
    return out


def decide_gate(m: dict) -> dict:
    check_counts(m)
    retention = _higher_is_better(m["retained"], m["retention_base"], RETENTION_MIN_SAMPLE, RETENTION_GREEN, RETENTION_AMBER)
    adoption = _higher_is_better(m["ai_users_14d"], m["active_users_14d"], ADOPTION_MIN_SAMPLE, ADOPTION_GREEN, ADOPTION_AMBER)

    num, den = m["ai_entries_corrected"], m["ai_entries_eligible"]
    correction = {"numerator": num, "denominator": den, "percent": pct(num, den), "min_sample": CORRECTION_MIN_SAMPLE,
                  "green_at": f"<= {CORRECTION_GREEN}%", "amber_at": f"<= {CORRECTION_AMBER}%"}
    if den < CORRECTION_MIN_SAMPLE:
        correction["status"] = "not_scored"                      # too few AI entries: reported, but does not block
    elif num * 100 <= CORRECTION_GREEN * den:
        correction["status"] = "green"
    elif num * 100 <= CORRECTION_AMBER * den:
        correction["status"] = "amber"
    else:
        correction["status"] = "red"

    users = m["upgrade_interest_users"]
    interest = {"users": users, "green_at": f">= {UPGRADE_GREEN_USERS} users",
                "status": "green" if users >= UPGRADE_GREEN_USERS else "amber"}      # never red alone

    metrics = {"week4_retention": retention, "ai_adoption": adoption, "ai_correction": correction, "upgrade_interest": interest}
    reasons = []
    if retention["status"] == "inconclusive" or adoption["status"] == "inconclusive":
        verdict = "inconclusive"
        if retention["status"] == "inconclusive":
            reasons.append(f"retention has {retention['denominator']} activated starters; {RETENTION_MIN_SAMPLE} are needed")
        if adoption["status"] == "inconclusive":
            reasons.append(f"AI adoption has {adoption['denominator']} active users; {ADOPTION_MIN_SAMPLE} are needed")
    else:
        statuses = [x["status"] for x in metrics.values()]            # "not_scored" is neither red nor amber, so it cannot block
        verdict = "red" if "red" in statuses else "amber" if "amber" in statuses else "green"
        for name, x in metrics.items():
            if x["status"] in ("red", "amber"):
                reasons.append(f"{name} is {x['status']}")
        if correction["status"] == "not_scored":
            reasons.append(f"AI correction not scored: {den} AI entries, {CORRECTION_MIN_SAMPLE} are needed")
    return {"verdict": verdict, "metrics": metrics, "reasons": reasons}
