"""The release checklist must not drift from the code it talks about."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKLIST = (ROOT / "docs/RELEASE_CHECKLIST.md").read_text(encoding="utf-8")


def test_scripts_named_in_the_checklist_exist():
    for rel in re.findall(r"scripts\\([A-Za-z0-9_]+\.(?:py|ps1))", CHECKLIST):
        assert (ROOT / "scripts" / rel).exists(), rel


def test_environment_variables_named_in_the_checklist_are_read_by_the_code():
    code = "\n".join(p.read_text(encoding="utf-8") for p in list((ROOT / "app").rglob("*.py")) + [ROOT / "main.py"])
    for name in ("DATABASE_URL", "SECRET_KEY", "GROQ_API_KEY", "FINNHUB_API_KEY", "METAL_API_KEY",
                 "PRIVACY_CONTACT_EMAIL", "AUTO_MIGRATE", "LANGSMITH_TRACING", "ENV"):
        assert name in CHECKLIST and name in code, name
    assert "SUNNAH_API_KEY" in CHECKLIST          # read by the code since Path step 1; step 2 works with or without it


def test_the_checklist_keeps_backups_within_the_privacy_promise():
    assert "-Keep 4" in CHECKLIST and "30 days or less" in CHECKLIST
    page = (ROOT / "frontend/templates/pages/privacy.html").read_text(encoding="utf-8")
    assert "30 days or less" in page


def test_the_checklist_has_a_utc_launch_timestamp_and_the_gate_commands():
    assert "ToUniversalTime()" in CHECKLIST
    assert "--mode c1" in CHECKLIST and "--mode gate" in CHECKLIST and "--cohort-start" in CHECKLIST


def test_the_checklist_contains_no_secret_looking_values():
    assert not re.search(r"(sk|gsk|pk)_[A-Za-z0-9]{16,}", CHECKLIST)
    assert "postgresql://" not in CHECKLIST.replace("postgresql://.../shariahease_test", "")
