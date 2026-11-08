"""
Gate report: checkpoint C1 and Gate G (hand-off section 9). Run it on your machine against the PRODUCTION Postgres.

    python scripts/gate_report.py --mode c1   --cohort-start 2026-12-01T00:00:00Z
    python scripts/gate_report.py --mode gate --cohort-start 2026-12-01T00:00:00Z --as-of 2027-04-15T00:00:00Z

  --cohort-start  the launch moment (UTC, e.g. 2026-12-01T00:00:00Z). Write it down on launch day (docs/RELEASE_CHECKLIST.md).
  --as-of         the moment you are judging at; default: now. For Gate G use 30 days after the AI steps went live.
  --database-url  default: DATABASE_URL from the environment or from the .env in the folder you run this from.
                  The URL is never printed or saved.
  --out-dir       default gate_reports/ (git-ignored). A JSON file is written for EVERY run and never overwritten.

It only reads (the transaction is READ ONLY, the session time zone is UTC) and needs PostgreSQL: the metrics use time-zone
functions that SQLite does not have. Exit code 0 = the report was produced (whatever the verdict); 2 = it could not run.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gate_decision  # noqa: E402

REPORT_VERSION = 1
SQL_FILE = HERE / "gate_metrics.sql"
DAY = timedelta(days=1)


class ReportError(Exception):
    """A problem the user can fix; main() prints it and exits with 2."""


def parse_utc(text: str, what: str) -> datetime:
    """ISO-8601 with an explicit time zone (Z or +hh:mm). A date without a zone is refused: guessing a zone would move the cohort."""
    try:
        value = datetime.fromisoformat(text.strip().replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        raise ReportError(f"{what}: {text!r} is not an ISO-8601 date-time, for example 2026-12-01T00:00:00Z")
    if value.tzinfo is None:
        raise ReportError(f"{what}: {text!r} has no time zone; add Z for UTC, for example 2026-12-01T00:00:00Z")
    return value.astimezone(timezone.utc)


def normalise_url(raw: str) -> str:
    for prefix in ("postgres://", "postgresql://"):
        if raw.startswith(prefix):
            return "postgresql+psycopg://" + raw[len(prefix):]
    return raw


def parameters(mode: str, cohort_start: datetime, as_of: datetime) -> dict:
    if as_of <= cohort_start:
        raise ReportError("--as-of must be after --cohort-start")
    cohort_end = as_of - 28 * DAY if mode == "gate" else as_of
    if mode == "gate" and cohort_end <= cohort_start:
        raise ReportError("for Gate G, --as-of must be more than 28 days after --cohort-start (nobody can be 4 weeks old yet)")
    return {"cohort_start": cohort_start, "as_of": as_of, "cohort_end": cohort_end}


def run_sql(engine, params: dict) -> dict:
    """Run gate_metrics.sql in a READ ONLY transaction with the session time zone set to UTC; returns one dict of counts."""
    from sqlalchemy import text
    sql = SQL_FILE.read_text(encoding="utf-8")
    with engine.connect() as conn:
        try:
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            conn.exec_driver_sql("SET LOCAL TIME ZONE 'UTC'")
            row = conn.execute(text(sql), params).mappings().one()
        finally:
            conn.rollback()
    return {k: (None if v is None else int(v)) for k, v in row.items()}


def build_report(mode: str, params: dict, counts: dict, generated_at: datetime) -> dict:
    count_part = {k: counts[k] for k in gate_decision.COUNT_KEYS}
    decision = gate_decision.decide_c1(count_part) if mode == "c1" else gate_decision.decide_gate(count_part)
    return {
        "report_version": REPORT_VERSION,
        "mode": mode,
        "generated_at": generated_at.isoformat(),
        "cohort_start": params["cohort_start"].isoformat(),
        "as_of": params["as_of"].isoformat(),
        "cohort_end": params["cohort_end"].isoformat(),
        "counts": count_part,
        "informational": {
            "signup_to_first_entry_median_minutes": counts.get("signup_to_first_entry_median_minutes"),
            "limit_hit": None,
            "limit_hit_note": "not available: the usage_counters table arrives with Path step 4",
        },
        "decision": decision,
    }


def write_report(report: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = lambda s: datetime.fromisoformat(s).strftime("%Y%m%dT%H%M%SZ")
    base = f"gate-{report['mode']}-asof{stamp(report['as_of'])}-run{stamp(report['generated_at'])}"
    path = out_dir / f"{base}.json"
    n = 1
    while path.exists():                               # never overwrite an earlier run
        n += 1
        path = out_dir / f"{base}-{n}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return path


def render(report: dict) -> str:
    c, d = report["counts"], report["decision"]
    lines = [
        f"ShariahEase {'checkpoint C1' if report['mode'] == 'c1' else 'Gate G'}  (as of {report['as_of']})",
        f"cohort: first expense from {report['cohort_start']} up to {report['cohort_end']}",
        "",
    ]
    if report["mode"] == "c1":
        lines += [
            f"starters (first expense in the cohort) : {c['starters']}",
            f"  old enough to judge (7+ days)        : {c['starters_judgeable']}",
            f"  activated (3+ entries, 2+ days)      : {c['activated']}  ({d['activated_percent']})",
            "",
            "DECISION: " + ("PROCEED to the AI steps" if d["proceed"] else "DO NOT PROCEED yet"),
        ] + [f"  - {r}" for r in d["reasons"]]
    else:
        m = d["metrics"]
        r, a, k, u = m["week4_retention"], m["ai_adoption"], m["ai_correction"], m["upgrade_interest"]
        lines += [
            f"starters {c['starters']}, activated {c['activated']} of {c['starters_judgeable']} judgeable",
            f"week-4 retention : {r['numerator']}/{r['denominator']} = {r['percent']:>6}   {r['status'].upper()}  (green {r['green_at']}, amber {r['amber_at']}, min sample {r['min_sample']})",
            f"AI adoption      : {a['numerator']}/{a['denominator']} = {a['percent']:>6}   {a['status'].upper()}  (green {a['green_at']}, amber {a['amber_at']}, min sample {a['min_sample']})",
            f"AI correction    : {k['numerator']}/{k['denominator']} = {k['percent']:>6}   {k['status'].upper()}  (green {k['green_at']}, amber {k['amber_at']}, min sample {k['min_sample']})",
            f"upgrade interest : {u['users']} users   {u['status'].upper()}  (green {u['green_at']})",
            "",
            f"VERDICT: {d['verdict'].upper()}",
        ] + [f"  - {x}" for x in d["reasons"]]
    info = report["informational"]
    lines += ["", f"info: signup to first expense (lower median): {info['signup_to_first_entry_median_minutes']} minutes; limit hits: n/a ({info['limit_hit_note']})"]
    return "\n".join(lines)


def main(argv=None, engine_factory=None, now=None) -> int:
    ap = argparse.ArgumentParser(description="ShariahEase gate report (C1 / Gate G). Read-only.")
    ap.add_argument("--mode", choices=["c1", "gate"], required=True)
    ap.add_argument("--cohort-start", required=True)
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--database-url", default=None)
    ap.add_argument("--out-dir", default="gate_reports")
    args = ap.parse_args(argv)
    try:
        generated_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        cohort_start = parse_utc(args.cohort_start, "--cohort-start")
        as_of = parse_utc(args.as_of, "--as-of") if args.as_of else generated_at
        params = parameters(args.mode, cohort_start, as_of)

        url = args.database_url
        if not url:
            try:
                from dotenv import load_dotenv
                load_dotenv(Path.cwd() / ".env")      # the .env of the folder you run it from: no hidden search up the tree
            except ImportError:
                pass
            url = os.getenv("DATABASE_URL", "")
        if not url:
            raise ReportError("no database: set DATABASE_URL (or .env) to the production Postgres, or pass --database-url")
        url = normalise_url(url)
        if not url.startswith("postgresql"):
            raise ReportError("the gate report needs PostgreSQL (it uses time-zone functions); DATABASE_URL is not a Postgres URL")

        if engine_factory is None:
            from sqlalchemy import create_engine
            engine_factory = create_engine
        engine = engine_factory(url)
        try:
            counts = run_sql(engine, params)
        finally:
            engine.dispose()
        report = build_report(args.mode, params, counts, generated_at)
        path = write_report(report, Path(args.out_dir))
    except ReportError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except ValueError as e:                            # inconsistent counts: stop, no verdict
        print(f"error: the numbers do not add up, no verdict given: {e}", file=sys.stderr)
        return 2
    except Exception as e:                             # a connection problem etc.; the URL (and password) is never printed
        print(f"error: could not run the report ({type(e).__name__}). Check DATABASE_URL and that the database is reachable.", file=sys.stderr)
        return 2
    print(render(report))
    print(f"\nsaved: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
