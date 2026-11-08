"""
Mutation check for Path step 1 (fix batch): the guards' greeting rule, the SUNNAH_API_KEY handling, the runtime year in
search queries, and the edge-tts line in requirements.txt.

For each mutation: copy the repo to a temp folder, break ONE thing in the copy, run the step 1 tests, and require
them to FAIL. A mutation that survives means a test is missing.

    python scripts/mutation_check_step01.py            # all mutations (a few seconds each; run in slices on a slow machine)
    python scripts/mutation_check_step01.py --check    # only verify that every mutation applies exactly once
    python scripts/mutation_check_step01.py --slice 0:10
    python scripts/mutation_check_step01.py --only year:  # only mutations whose label contains this text
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "*.db", "venv", ".venv", ".pytest_cache", "knowledge_base")
G = "app/services/greeting.py"
LLM = "app/services/llm_client.py"
WA = "app/services/whatsapp_service.py"
WS = "app/services/web_search.py"
REQ = "requirements.txt"

# (file, old, new, label)
MUTATIONS = [
    # --- greeting helper ---------------------------------------------------------------------------
    (G, 'return re.compile(r"(?<!\\w)(?:" + "|".join(parts) + ")")', 'return re.compile(r"(?:" + "|".join(parts) + ")")', "greeting: may start inside a word (machine)"),
    (G, 'return body if stem else body + r"(?!\\w)"', "return body", "greeting: may end inside a word (history)"),
    (G, "    if term[-1].isascii() and term[-1].isalpha():", "    if False:", "greeting: no stretched spellings (hii, heyy)"),
    (G, 're.escape(term[-1]) + "+"', 're.escape(term[-1]) + "*"', "greeting: last letter optional (he, hell)"),
    (G, '    return bool(pattern and pattern.search(text.lower()))', '    return bool(pattern and pattern.search(text))', "greeting: case matters"),
    (G, "    if not parts:\n        return None\n", "", "greeting: an empty list matches everything"),
    (G, 'return body if stem else body + r"(?!\\w)"', 'return body + r"(?!\\w)"', "greeting: stems must end the word (assalamu)"),
    # --- web guard (llm_client) --------------------------------------------------------------------
    (LLM, "    if contains_greeting(lower, _GREETINGS, _GREETING_STEMS):\n        return False", "    if False:\n        return False", "web guard: greetings are blocked"),
    (LLM, "    if contains_greeting(lower, _GREETINGS, _GREETING_STEMS):", "    if any(g in lower for g in _GREETINGS):", "web guard: back to substring matching"),
    (LLM, '_GREETING_STEMS = {"assalam"}', "_GREETING_STEMS = set()", "web guard: assalamu / assalamo not accepted"),
    (LLM, '_GREETING_STEMS = {"assalam"}', '_GREETING_STEMS = {"assalam", "hi"}', "web guard: 'hi' accepted as a word start (hiking)"),
    (LLM, '    "hello", "hi", "hey", "salam",', '    "hello", "hey", "salam",', "web guard: 'hi' is not a greeting"),
    # --- WhatsApp guard ----------------------------------------------------------------------------
    (WA, "    if contains_greeting(lower, _GREETING_RESPONSES, _GREETING_STEMS):\n        return True", "    if False:\n        return True", "whatsapp: greetings are blocked"),
    (WA, "    if contains_greeting(lower, _GREETING_RESPONSES, _GREETING_STEMS):", "    if any(g in lower for g in _GREETING_RESPONSES):", "whatsapp: back to substring matching"),
    (WA, '_GREETING_STEMS = {"assalam", "walaikum"}', '_GREETING_STEMS = {"assalam"}', "whatsapp: walaikumassalam not accepted"),
    (WA, '_GREETING_STEMS = {"assalam", "walaikum"}', '_GREETING_STEMS = {"assalam", "walaikum", "hi"}', "whatsapp: 'hi' accepted as a word start (hiking)"),
    # --- SUNNAH_API_KEY ----------------------------------------------------------------------------
    (WS, "    if not api_key:\n        if not _sunnah_key_warned:", "    if False:\n        if not _sunnah_key_warned:", "key: a request is made without the key"),
    (WS, 'return os.getenv("SUNNAH_API_KEY", "").strip()', 'return os.getenv("SUNNAH_API_KEY", "")', "key: a blank key counts as set"),
    (WS, 'return os.getenv("SUNNAH_API_KEY", "").strip()', 'return os.getenv("SUNAH_API_KEY", "").strip()', "key: wrong variable name"),
    (WS, 'headers={"X-API-Key": api_key},', 'headers={"X-API-Key": "dummy-key-0000000000"},', "key: a fixed key is sent"),
    (WS, "            _sunnah_key_warned = True\n", "            pass\n", "key: the warning repeats on every call"),
    (WS, "        if not _sunnah_key_warned:", "        if True:", "key: the warning is never limited to once"),
    (WS, "        return _SUNNAH_UNAVAILABLE\n", '        return ""\n', "key: the answer does not say the lookup is unavailable"),
    (WS, "=== HADITH LOOKUP UNAVAILABLE ===", "=== NOTE ===", "key: the unavailable block loses its marker"),
    # --- the year ----------------------------------------------------------------------------------
    (WS, 'today {_current_year()}"', 'today 2025"', "year: price query with a company"),
    (WS, 'PSX share price {_current_year()}"', 'PSX share price 2025"', "year: price query without a company"),
    (WS, 'sarafa {_current_year()}"', 'sarafa 2025"', "year: gold and silver query"),
    (WS, 'Islamic finance {_current_year()}"', 'Islamic finance 2025"', "year: finance news query"),
    (WS, "return datetime.now(timezone.utc).year", "return datetime.now(timezone.utc).year - 1", "year: helper returns the wrong year"),
    # --- requirements.txt --------------------------------------------------------------------------
    (REQ, "edge-tts==7.3.1\n", "", "requirements: edge-tts missing"),
    (REQ, "edge-tts==7.3.1\n", "edge-tts\n", "requirements: edge-tts not pinned"),
]

TESTS = ["tests/test_guard_greeting.py", "tests/test_web_search_config.py", "tests/test_guard.py"]


def run_tests(repo: Path) -> bool:
    cmd = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *TESTS]
    try:
        return subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=240).returncode == 0
    except subprocess.TimeoutExpired:          # a hang is a failure too: the broken code is noticed
        return False


def _apply(text: str, old: str, new: str, label: str):
    if text.count(old) != 1:
        return None, f"BAD MUTATION (found {text.count(old)}x): {label}"
    return text.replace(old, new), None


def main() -> int:
    check_only = "--check" in sys.argv
    lo, hi = 0, len(MUTATIONS)
    if "--slice" in sys.argv:
        a, b = sys.argv[sys.argv.index("--slice") + 1].split(":")
        lo, hi = int(a or 0), int(b or len(MUTATIONS))
    chosen = MUTATIONS[lo:hi]
    if "--only" in sys.argv:
        needle = sys.argv[sys.argv.index("--only") + 1]
        chosen = [m for m in chosen if needle in m[3]]
    bad = []
    for rel, old, new, label in MUTATIONS:
        _, problem = _apply((ROOT / rel).read_text(encoding="utf-8"), old, new, label)
        if problem:
            bad.append(problem)
    if bad:
        print("\n".join(bad))
        return 2
    if check_only:
        print(f"all {len(MUTATIONS)} mutations apply exactly once")
        return 0

    survived, ran = [], 0
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "base"
        shutil.copytree(ROOT, base, ignore=IGNORE)
        (base / "knowledge_base").mkdir(exist_ok=True)
        if not run_tests(base):
            print("The unmodified tests already fail; fix that first.")
            return 2
        for rel, old, new, label in chosen:
            work = Path(tmp) / "work"
            if work.exists():
                shutil.rmtree(work)
            shutil.copytree(base, work)
            text, _ = _apply((work / rel).read_text(encoding="utf-8"), old, new, label)
            (work / rel).write_text(text, encoding="utf-8")
            caught = not run_tests(work)
            ran += 1
            print(("CAUGHT   " if caught else "SURVIVED ") + label, flush=True)
            if not caught:
                survived.append(label)
    print(f"\n{ran - len(survived)}/{ran} mutations caught")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
