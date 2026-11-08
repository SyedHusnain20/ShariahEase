"""scripts/gate_report.py without a database: arguments, report files, safety. (The SQL itself is tested in test_gate_sql_pg.py.)"""
import importlib.util
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("gate_report_t", SCRIPTS / "gate_report.py")
gr = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gr)

START = "2026-12-01T00:00:00Z"
NOW = datetime(2027, 4, 20, 12, 0, tzinfo=timezone.utc)
FAKE_USER, FAKE_PASSWORD, FAKE_HOST = "appuser", "fake-" + "pass-for-tests", "db.example.invalid"      # built from parts: not a real credential
SECRET_URL = f"postgresql://{FAKE_USER}:{FAKE_PASSWORD}@{FAKE_HOST}/prod?sslmode=require"

GOOD = dict(starters=120, starters_judgeable=100, activated=40, retention_base=40, retained=14, active_users_14d=60,
            ai_users_14d=20, ai_entries_eligible=80, ai_entries_corrected=15, upgrade_interest_users=12,
            signup_to_first_entry_median_minutes=45)


class FakeEngine:
    def dispose(self):
        pass


class RealDatabaseTouched(BaseException):
    """Not an Exception on purpose: main() catches Exception, and this must never be swallowed into a normal exit code."""


@pytest.fixture(autouse=True)
def never_open_a_real_database(monkeypatch):
    """A test in this file must never reach a real database (a developer's .env may point at a real one)."""
    import sqlalchemy

    def refuse(*a, **k):
        raise RealDatabaseTouched("a test tried to open a real database connection")
    monkeypatch.setattr(sqlalchemy, "create_engine", refuse)


@pytest.fixture
def run(monkeypatch, tmp_path):
    """Call main() with a fake database layer; returns (exit code, stdout, stderr, out_dir)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    seen = {}

    def go(argv, counts=None, url=SECRET_URL, capsys=None):
        monkeypatch.setattr(gr, "run_sql", lambda engine, params: (seen.update(params=params) or dict(counts or GOOD)))
        code = gr.main(["--database-url", url, "--out-dir", str(tmp_path / "out")] + argv,
                       engine_factory=lambda u: (seen.update(url=u) or FakeEngine()), now=NOW)
        return code, seen
    return go


def files(tmp_path):
    return sorted((tmp_path / "out").glob("*.json")) if (tmp_path / "out").exists() else []


# ── dates ────────────────────────────────────────────────────────────────────────

def test_parse_utc():
    assert gr.parse_utc("2026-12-01T00:00:00Z", "x") == datetime(2026, 12, 1, tzinfo=timezone.utc)
    assert gr.parse_utc("2026-12-01T05:00:00+05:00", "x") == datetime(2026, 12, 1, 0, 0, tzinfo=timezone.utc)
    assert gr.parse_utc("2026-12-01T00:00:00z", "x").tzinfo is not None


@pytest.mark.parametrize("bad", ["2026-12-01", "2026-12-01T00:00:00", "tomorrow", "", "2026-13-01T00:00:00Z"])
def test_parse_utc_refuses_guesses(bad):
    with pytest.raises(gr.ReportError):
        gr.parse_utc(bad, "--cohort-start")


def test_parameters():
    s = datetime(2026, 12, 1, tzinfo=timezone.utc)
    a = datetime(2027, 4, 1, tzinfo=timezone.utc)
    p = gr.parameters("gate", s, a)
    assert p["cohort_end"] == a - timedelta(days=28) and p["cohort_start"] == s
    assert gr.parameters("c1", s, a)["cohort_end"] == a
    for mode, as_of in (("c1", s), ("c1", s - timedelta(days=1)), ("gate", s + timedelta(days=28)), ("gate", s + timedelta(days=10))):
        with pytest.raises(gr.ReportError):
            gr.parameters(mode, s, as_of)
    assert gr.parameters("gate", s, s + timedelta(days=28, seconds=1))["cohort_end"] > s


def test_normalise_url():
    assert gr.normalise_url("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert gr.normalise_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert gr.normalise_url("postgresql+psycopg://u:p@h/db") == "postgresql+psycopg://u:p@h/db"


# ── a normal run ─────────────────────────────────────────────────────────────────

def test_a_gate_run_prints_a_verdict_and_saves_a_file(run, tmp_path, capsys):
    code, seen = run(["--mode", "gate", "--cohort-start", START])
    out = capsys.readouterr().out
    assert code == 0
    assert "VERDICT:" in out and "week-4 retention" in out and "AI correction" in out and "saved:" in out
    [path] = files(tmp_path)
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["mode"] == "gate" and report["report_version"] == 1
    assert report["counts"]["starters"] == 120 and report["decision"]["verdict"] in ("green", "amber", "red", "inconclusive")
    assert report["as_of"] == NOW.isoformat() and report["cohort_end"] == (NOW - timedelta(days=28)).isoformat()
    assert report["informational"]["signup_to_first_entry_median_minutes"] == 45
    assert report["informational"]["limit_hit"] is None and "step 4" in report["informational"]["limit_hit_note"]
    assert seen["params"]["cohort_end"] == NOW - timedelta(days=28)
    assert seen["url"].startswith("postgresql+psycopg://")


def test_a_c1_run(run, tmp_path, capsys):
    code, seen = run(["--mode", "c1", "--cohort-start", START])
    out = capsys.readouterr().out
    assert code == 0 and "DECISION: PROCEED" in out
    [path] = files(tmp_path)
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["decision"]["proceed"] is True and report["cohort_end"] == NOW.isoformat()


def test_a_c1_run_that_says_no(run, tmp_path, capsys):
    code, _ = run(["--mode", "c1", "--cohort-start", START], counts=dict(GOOD, starters=12, starters_judgeable=4, activated=0,
                                                                         retention_base=0, retained=0))
    out = capsys.readouterr().out
    assert code == 0 and "DO NOT PROCEED" in out and "only 12 users" in out


def test_explicit_as_of(run, tmp_path):
    code, seen = run(["--mode", "gate", "--cohort-start", START, "--as-of", "2027-03-01T00:00:00Z"])
    assert code == 0 and seen["params"]["as_of"] == datetime(2027, 3, 1, tzinfo=timezone.utc)


def test_every_run_is_kept_and_none_is_overwritten(run, tmp_path):
    for _ in range(3):
        assert run(["--mode", "gate", "--cohort-start", START])[0] == 0
    names = [p.name for p in files(tmp_path)]
    assert len(names) == 3 and len(set(names)) == 3
    assert all(re.fullmatch(r"gate-gate-asof\d{8}T\d{6}Z-run\d{8}T\d{6}Z(-\d+)?\.json", n) for n in names), names


def test_the_report_file_is_plain_ascii(run, tmp_path):
    run(["--mode", "gate", "--cohort-start", START])
    [path] = files(tmp_path)
    path.read_bytes().decode("ascii")


def test_the_console_output_is_plain_ascii_so_windows_consoles_cope(run, capsys):
    run(["--mode", "gate", "--cohort-start", START])
    capsys.readouterr().out.encode("ascii")
    run(["--mode", "c1", "--cohort-start", START])
    capsys.readouterr().out.encode("ascii")


# ── refusals ─────────────────────────────────────────────────────────────────────

def test_a_sqlite_url_is_refused_without_connecting(run, tmp_path, capsys):
    code, seen = run(["--mode", "gate", "--cohort-start", START], url="sqlite:///dev.db")
    err = capsys.readouterr().err
    assert code == 2 and "PostgreSQL" in err and "url" not in seen and not files(tmp_path)


def test_no_database_is_refused(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert gr.main(["--mode", "c1", "--cohort-start", START, "--out-dir", str(tmp_path / "o")], now=NOW) == 2
    assert "DATABASE_URL" in capsys.readouterr().err


def test_the_env_file_is_read_from_the_current_folder_only(monkeypatch, tmp_path):
    """Regression: load_dotenv() with no argument searches up from the SCRIPT's folder, so a .env in the repository
    root was found even when a test (or you) ran from somewhere else, and the report reached a real database."""
    import dotenv
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    asked = []
    monkeypatch.setattr(dotenv, "load_dotenv", lambda path=None, *a, **k: asked.append(path) or False)
    assert gr.main(["--mode", "c1", "--cohort-start", START, "--out-dir", str(tmp_path / "o")], now=NOW) == 2
    assert asked == [tmp_path / ".env"]


def test_a_database_url_in_the_current_folders_env_file_is_used(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    (tmp_path / ".env").write_text("DATABASE_URL=sqlite:///from-the-env-file.db\n", encoding="utf-8")
    assert gr.main(["--mode", "c1", "--cohort-start", START, "--out-dir", str(tmp_path / "o")], now=NOW) == 2
    err = capsys.readouterr().err
    assert "PostgreSQL" in err and "no database" not in err           # it found the URL in the file, then refused SQLite


def test_the_safety_net_really_blocks_a_real_connection_even_through_main(monkeypatch, tmp_path):
    import sqlalchemy
    with pytest.raises(RealDatabaseTouched):
        sqlalchemy.create_engine("postgresql://u:p@h/db")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gr, "run_sql", lambda e, p: dict(GOOD))
    with pytest.raises(RealDatabaseTouched):                 # not swallowed by main()'s "could not run the report" handler
        gr.main(["--mode", "c1", "--cohort-start", START, "--database-url", "postgresql://u:p@h/db", "--out-dir", str(tmp_path / "o")], now=NOW)


def test_bad_dates_are_refused(run, tmp_path, capsys):
    assert run(["--mode", "gate", "--cohort-start", "2026-12-01"])[0] == 2
    assert "no time zone" in capsys.readouterr().err
    assert run(["--mode", "gate", "--cohort-start", START, "--as-of", "2026-12-10T00:00:00Z"])[0] == 2       # under 28 days
    assert "28 days" in capsys.readouterr().err
    assert not files(tmp_path)


def test_inconsistent_numbers_give_no_verdict_and_no_file(run, tmp_path, capsys):
    code, _ = run(["--mode", "gate", "--cohort-start", START], counts=dict(GOOD, retained=99, retention_base=40))
    assert code == 2 and "no verdict" in capsys.readouterr().err and not files(tmp_path)


def test_the_database_url_never_appears_in_output_or_files(run, tmp_path, capsys, monkeypatch):
    run(["--mode", "gate", "--cohort-start", START])
    captured = capsys.readouterr()
    text = captured.out + captured.err + "".join(p.read_text(encoding="utf-8") for p in files(tmp_path))
    for secret in (FAKE_PASSWORD, FAKE_USER, FAKE_HOST, "postgresql"):
        assert secret not in text

    def broken(engine, params):
        raise RuntimeError(f"could not connect to {SECRET_URL}")
    monkeypatch.setattr(gr, "run_sql", broken)
    code = gr.main(["--mode", "gate", "--cohort-start", START, "--database-url", SECRET_URL, "--out-dir", str(tmp_path / "x")],
                   engine_factory=lambda u: FakeEngine(), now=NOW)
    err = capsys.readouterr().err
    assert code == 2 and "RuntimeError" in err and FAKE_PASSWORD not in err and FAKE_HOST not in err


# ── the SQL file and the code agree ──────────────────────────────────────────────

def test_the_sql_returns_every_count_the_decision_needs():
    sql = (SCRIPTS / "gate_metrics.sql").read_text(encoding="utf-8")
    sys.path.insert(0, str(SCRIPTS))
    import gate_decision
    for key in gate_decision.COUNT_KEYS + ("signup_to_first_entry_median_minutes",):
        assert re.search(rf"\bAS {key}\b", sql), key
    for param in (":cohort_start", ":as_of", ":cohort_end"):
        assert param in sql


def test_run_sql_sets_a_read_only_utc_session():
    src = (SCRIPTS / "gate_report.py").read_text(encoding="utf-8")
    assert "SET TRANSACTION READ ONLY" in src and "SET LOCAL TIME ZONE 'UTC'" in src and "conn.rollback()" in src
