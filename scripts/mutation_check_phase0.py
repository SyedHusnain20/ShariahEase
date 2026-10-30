"""
Mutation check for the Phase 0 security code.

For each mutation: copy the repo to a temp folder, break ONE thing in the copy, run the test suite,
and require it to FAIL. A mutation that survives means a test is missing.

    python scripts/mutation_check_phase0.py
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "*.db", "venv", ".venv", ".pytest_cache", "knowledge_base")

# (file, exact text to replace, replacement, label)
MUTATIONS = [
    # --- history scoping (the guest leak) ---
    ("app/database/crud.py", "    if user_id is None:\n        return []\n    owner", "    owner = True == True\n    if False:\n        return []\n    owner", "guest history unscoped (the original leak)"),
    ("app/database/crud.py", "owner = models.ChatMessage.user_id == user_id", "owner = True == True", "user history unscoped"),
    ("app/database/crud.py", "    if user_id is None:\n        return None\n", "", "guest messages written to the database"),
    ("app/routers/chatbot.py", "return gm.memory_key(guest_id, session_id)", "return gm.memory_key('shared', session_id)", "guest memory shared between browsers"),
    ("app/routers/chatbot.py", "        gm.guest_memory.add(key, \"user\", request.message)\n", "", "guest context not remembered"),
    ("app/services/guest_memory.py", "if now - seen > self.ttl]", "if False]", "guest memory never expires"),
    ("app/services/guest_memory.py", "messages[-self.max_messages:]", "messages", "guest memory unbounded per session"),
    ("app/services/guest_memory.py", "while len(self._data) > self.max_sessions:", "while False:", "guest memory unbounded in sessions"),
    ("alembic/versions/0003_purge_guest_chat.py", 'DELETE FROM chat_messages WHERE user_id IS NULL', 'DELETE FROM chat_messages', "purge deletes logged-in users' messages too"),
    ("scripts/copy_sqlite_to_postgres.py", 'if data.get("user_id") is None or data["user_id"] not in user_ids:', 'if False:', "copy script keeps guest messages"),
    # --- fail closed ---
    ("app/services/auth_service.py", 'if env.get("ENV", "development") == "production":', 'if False:', "production fail-closed disabled"),
    ("app/services/auth_service.py", "or len(key) < _MIN_PROD_KEY_LEN", "", "short production key accepted"),
    ("app/services/auth_service.py", "or key == _DEV_KEY ", "", "dev default key accepted in production"),
    # --- access token checks ---
    ("app/services/auth_service.py", 'if payload.get("typ") != "access":', 'if False:', "token type not checked"),
    ("app/services/auth_service.py", 'options={"require": ["exp", "sub"]},', 'options={"require": []},', "exp/sub no longer required"),
    ("app/services/auth_service.py", "algorithms=[ALGORITHM]", 'algorithms=["HS256", "HS384"]', "other signing algorithms allowed"),
    ("app/dependencies.py", 'if payload.get("tv") != user.token_version:', 'if False:', "token_version not checked"),
    # --- refresh tokens ---
    ("app/services/refresh_tokens.py", "        revoke_family(db, row.family_id, now)                     # reuse of an old token: assume theft\n", "", "reuse does not revoke the family"),
    ("app/services/refresh_tokens.py", "if recently and row.replaced_by is not None:", "if False:", "no grace window for concurrent tabs"),
    ("app/services/refresh_tokens.py", "if _aware(row.expires_at) <= now:", "if False:", "refresh expiry not checked"),
    ("app/services/refresh_tokens.py", "RefreshToken.id == row.id, RefreshToken.revoked_at.is_(None)\n    ).update({RefreshToken.revoked_at: now}", "RefreshToken.id == row.id\n    ).update({RefreshToken.revoked_at: now}", "rotation claim not atomic"),
    ("app/services/refresh_tokens.py", "token_hash=hash_token(raw),", "token_hash=raw,", "raw refresh token stored"),
    ("app/services/refresh_tokens.py", "family_id=row.family_id, token_hash=hash_token(new_raw)", "family_id=uuid.uuid4(), token_hash=hash_token(new_raw)", "rotation starts a new family"),
    ("app/routers/auth.py", "    current_user.token_version += 1\n", "", "logout-all does not bump token_version"),
    ("app/routers/auth.py", "    refresh_tokens.revoke_all_for_user(db, current_user.id)\n", "", "logout-all keeps refresh tokens"),
    ("app/routers/auth.py", "    refresh_tokens.revoke_presented_token(db, request.cookies.get(\"refresh_token\"))\n", "", "logout keeps the refresh token alive"),
    # --- webhook ---
    ("app/services/webhook_service.py", "return hmac.compare_digest(expected, header)", "return True", "signature always valid"),
    ("app/routers/webhook.py", "    elif _is_production():", "    elif False:", "production accepts unsigned webhooks"),
    ("app/routers/webhook.py", "    if state == \"duplicate\":", "    if False:", "replayed events are processed again"),
    ("app/routers/webhook.py", "mask_phone(sender)", "sender", "phone number logged in full"),
    ("app/services/webhook_service.py", "if production and (not configured or configured == DEFAULT_VERIFY_TOKEN):", "if False:", "default verify token accepted in production"),
    ("app/services/webhook_service.py", "WebhookEvent.status == \"failed\", WebhookEvent.attempts < MAX_ATTEMPTS,", "WebhookEvent.status == \"failed\",", "unlimited retries of a failed event"),
    ("app/routers/webhook.py", "error=type(exc).__name__)", "error=str(exc))", "exception text stored (may hold user content)"),
    # --- database ---
    ("app/database/db.py", "cur.execute(\"PRAGMA foreign_keys=ON\")", "cur.execute(\"PRAGMA foreign_keys=OFF\")", "SQLite foreign keys off"),
    ("app/database/migrate.py", "if \"users\" in names and \"alembic_version\" not in names:", "if False:", "legacy database not stamped"),
    ("tests/conftest.py", 'if "test" not in name:', "if False:", "pg tests may wipe a non-test database"),
    ("app/database/db.py", "pool_pre_ping=True,", "pool_pre_ping=False,", "Postgres pre-ping off"),
]


def run_tests(repo: Path) -> bool:
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"],
                       cwd=repo, capture_output=True, text=True)
    return r.returncode == 0


def main() -> int:
    survived = []
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "base"
        shutil.copytree(ROOT, base, ignore=IGNORE)
        (base / "knowledge_base").mkdir(exist_ok=True)
        if not run_tests(base):
            print("The unmodified tests already fail; fix that first.")
            return 2
        for rel, old, new, label in MUTATIONS:
            work = Path(tmp) / "work"
            if work.exists():
                shutil.rmtree(work)
            shutil.copytree(base, work)
            f = work / rel
            text = f.read_text(encoding="utf-8")
            if text.count(old) != 1:
                print(f"BAD MUTATION (found {text.count(old)}x): {label}")
                return 2
            f.write_text(text.replace(old, new), encoding="utf-8")
            caught = not run_tests(work)
            print(("CAUGHT   " if caught else "SURVIVED ") + label)
            if not caught:
                survived.append(label)
    print(f"\n{len(MUTATIONS) - len(survived)}/{len(MUTATIONS)} mutations caught")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
