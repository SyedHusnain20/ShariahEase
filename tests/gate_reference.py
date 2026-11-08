"""
An INDEPENDENT implementation of the gate counts (hand-off section 9), in plain Python over plain rows. It is written from
the definitions, not from the SQL, so that tests/test_gate_sql_pg.py can compare the two on random data: if they ever
disagree, one of them misreads the definition. Also holds the random data generator (it hugs every boundary on purpose).
"""
import random
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

DAY = timedelta(days=1)
AI_SOURCES = ("ai_chat", "whatsapp")
DEFAULT_TZ = "Asia/Karachi"
VALID_TZ = ["Asia/Karachi", "UTC", "America/New_York", "Pacific/Kiritimati", "Pacific/Pago_Pago", "Asia/Kolkata", "Australia/Adelaide", "Europe/London"]
INVALID_TZ = ["Not/AZone", "", "karachi"]


def _tz(name):
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def reference_counts(users, expenses, audit, cohort_start, as_of, cohort_end):
    """users: dicts id, created_at, tz, is_admin. expenses: id, user_id, created_at, source. audit: user_id, action, entity_type, entity_id, created_at."""
    people = {u["id"]: u for u in users if not u["is_admin"]}
    rows = defaultdict(list)
    for e in expenses:
        if e["user_id"] in people and e["created_at"] < as_of:
            rows[e["user_id"]].append(e)

    starters = []
    for uid, mine in rows.items():
        first = min(r["created_at"] for r in mine)
        if cohort_start <= first < cohort_end:
            starters.append((uid, first))
    judgeable = [(u, f) for u, f in starters if f <= as_of - 7 * DAY]

    activated = []
    for uid, first in judgeable:
        week = [r for r in rows[uid] if first <= r["created_at"] < first + 7 * DAY]
        zone = _tz(people[uid]["tz"])
        days = {r["created_at"].astimezone(zone).date() for r in week}
        if len(week) >= 3 and len(days) >= 2:
            activated.append((uid, first))

    base = [(u, f) for u, f in activated if f <= as_of - 28 * DAY]
    retained = sum(1 for u, f in base if any(f + 21 * DAY <= r["created_at"] < f + 28 * DAY for r in rows[u]))

    recent = {}
    for uid, mine in rows.items():
        window = [r for r in mine if as_of - 14 * DAY <= r["created_at"] < as_of]
        if window:
            recent[uid] = any(r["source"] in AI_SOURCES for r in window)

    eligible = [r for mine in rows.values() for r in mine
                if r["source"] in AI_SOURCES and as_of - 30 * DAY < r["created_at"] <= as_of - timedelta(hours=24)]
    corrected = 0
    for r in eligible:
        for a in audit:
            if (a["entity_type"] == "expense" and a["entity_id"] == str(r["id"]) and a["user_id"] == r["user_id"]
                    and a["action"] in ("expense.update", "expense.delete")
                    and r["created_at"] <= a["created_at"] < r["created_at"] + timedelta(hours=24) and a["created_at"] < as_of):
                corrected += 1
                break

    interest = {a["user_id"] for a in audit if a["action"] == "upgrade_interest" and a["user_id"] in people and a["created_at"] < as_of}

    gaps = sorted(int((f - people[u]["created_at"]).total_seconds() // 60) for u, f in starters)
    median = gaps[(len(gaps) + 1) // 2 - 1] if gaps else None          # the lower median

    return {
        "starters": len(starters), "starters_judgeable": len(judgeable), "activated": len(activated),
        "retention_base": len(base), "retained": retained,
        "active_users_14d": len(recent), "ai_users_14d": sum(1 for v in recent.values() if v),
        "ai_entries_eligible": len(eligible), "ai_entries_corrected": corrected,
        "upgrade_interest_users": len(interest), "signup_to_first_entry_median_minutes": median,
    }


# ── random data that hugs the boundaries ─────────────────────────────────────────────────────────

def _sec(rng, lo, hi):
    return lo + timedelta(seconds=rng.randint(0, int((hi - lo).total_seconds())))


def make_dataset(rng: random.Random, cohort_start, as_of, n_users=40):
    """Returns (users, expense_specs, audit_specs). Expenses are specs (key, user_id, created_at, source); audit rows point at expense keys."""
    cohort_end = as_of - 28 * DAY
    anchors = [cohort_start, cohort_end, as_of - 7 * DAY, as_of - 28 * DAY, as_of - 14 * DAY, as_of - 30 * DAY, as_of - timedelta(hours=24)]
    users, expenses, audit = [], [], []
    key = 0
    for uid in range(1, n_users + 1):
        tz = rng.choice(VALID_TZ + INVALID_TZ)
        is_admin = rng.random() < 0.1
        first = rng.choice([
            _sec(rng, cohort_start - 15 * DAY, as_of + 2 * DAY),
            rng.choice(anchors) + timedelta(seconds=rng.choice([-1, 0, 1])),
        ])
        signup = first - timedelta(seconds=rng.choice([0, 30, 59, 60, 61, 3600, 86400, rng.randint(0, 20 * 86400)]))
        users.append({"id": uid, "created_at": signup, "tz": tz, "is_admin": is_admin})

        def add(when, source=None):
            nonlocal key
            key += 1
            expenses.append({"key": key, "user_id": uid, "created_at": when,
                             "source": source or rng.choice(["manual", "manual", "manual", "pwa_sync", "ai_chat", "whatsapp"])})
            return key

        add(first)
        offsets = [timedelta(seconds=s) for s in (1, 3600, 7 * 86400 - 1, 7 * 86400, 7 * 86400 + 1, 21 * 86400 - 1, 21 * 86400,
                                                  28 * 86400 - 1, 28 * 86400, 28 * 86400 + 5)]
        for _ in range(rng.randint(0, 6)):
            add(first + rng.choice(offsets + [timedelta(seconds=rng.randint(0, 40 * 86400))]))
        for _ in range(rng.randint(0, 3)):                                  # entries around the AI windows
            base = rng.choice([as_of - 14 * DAY, as_of - 30 * DAY, as_of - timedelta(hours=24), as_of])
            k = add(base + timedelta(seconds=rng.choice([-2, -1, 0, 1, 2, rng.randint(-5 * 86400, 5 * 86400)])),
                    rng.choice(["ai_chat", "whatsapp", "manual"]))
            for _ in range(rng.randint(0, 3)):                              # corrections around the 24 h line
                t0 = next(e["created_at"] for e in expenses if e["key"] == k)
                audit.append({"user_id": rng.choice([uid, uid, uid, rng.randint(1, n_users)]), "action": rng.choice(
                    ["expense.update", "expense.delete", "expense.restore", "expense.create", "expense.undo"]),
                    "entity_type": rng.choice(["expense", "expense", "expense", "zakat"]), "entity_key": k,
                    "created_at": t0 + timedelta(seconds=rng.choice([-1, 0, 1, 86399, 86400, 86401, rng.randint(0, 3 * 86400)]))})
        zone = _tz(tz)                                                       # two entries one second apart across local midnight
        if rng.random() < 0.5:
            d = (first + timedelta(days=rng.randint(0, 5))).astimezone(zone).date()
            midnight = datetime(d.year, d.month, d.day, tzinfo=zone).astimezone(timezone.utc)
            add(midnight - timedelta(seconds=1))
            add(midnight)
        if rng.random() < 0.3:
            audit.append({"user_id": uid, "action": "upgrade_interest", "entity_type": None, "entity_key": None,
                          "created_at": as_of + timedelta(seconds=rng.choice([-1, 0, 1, -86400, 86400]))})
    return users, expenses, audit
