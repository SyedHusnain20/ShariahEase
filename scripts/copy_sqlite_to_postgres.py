"""
One-time copy of an existing ShariahEase SQLite file into the new database (e.g. Neon).

    python scripts/copy_sqlite_to_postgres.py --sqlite .\\shariahease.db --target "postgresql://user:pass@host/db?sslmode=require"

* Runs the Alembic migrations on the target first.
* Refuses to run if the target already has users (use --force to append anyway).
* Keeps the original ids, so every link between tables stays valid, then fixes the id counters.
* Guest chat messages (no user) and messages whose user no longer exists are NOT copied: guest
  messages are no longer stored.
* Prints counts only. It never prints names, emails, messages or any other row content.
* Reads the SQLite file read-only. Nothing in the source is changed.
"""
import argparse
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Table -> columns that exist in the OLD schema (new Phase 0 columns take their defaults).
TABLES = [
    ("users", ["id", "name", "email", "hashed_password", "language", "created_at"]),
    ("zakat_records", ["id", "user_id", "user_name", "cash", "gold_value", "silver_value", "business_inventory",
                       "receivables", "debts", "immediate_expenses", "total_assets", "total_deductions",
                       "zakatable_wealth", "nisab_threshold", "zakat_due", "is_zakat_due", "nisab_rate_used",
                       "calculated_at"]),
    ("screener_history", ["id", "user_id", "query", "verdict", "explanation", "screened_at"]),
    ("chat_messages", ["id", "user_id", "session_id", "role", "content", "language", "created_at"]),
]


def normalise_url(url: str) -> str:
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def copy_database(source_engine, target_engine, force: bool = False) -> dict:
    from app.database.migrate import upgrade_to_head
    upgrade_to_head(target_engine)

    counts = {}
    with source_engine.connect() as src, target_engine.begin() as dst:
        if not force and dst.execute(text("SELECT count(*) FROM users")).scalar():
            raise SystemExit("Target already has users. Nothing copied. Use --force to append anyway.")
        user_ids = set()
        for table, cols in TABLES:
            col_list = ", ".join(cols)
            rows = src.execute(text(f"SELECT {col_list} FROM {table} ORDER BY id")).mappings().all()
            nulled = skipped = copied = 0
            for row in rows:
                data = dict(row)
                if table == "users":
                    user_ids.add(data["id"])
                elif table == "chat_messages":
                    if data.get("user_id") is None or data["user_id"] not in user_ids:
                        skipped += 1                # guest message, or its user is gone: not kept
                        continue
                elif data.get("user_id") is not None and data["user_id"] not in user_ids:
                    data["user_id"] = None          # orphan from before foreign keys were enforced
                    nulled += 1
                placeholders = ", ".join(f":{c}" for c in cols)
                dst.execute(text(f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"), data)
                copied += 1
            counts[table] = {"copied": copied, "orphans_detached": nulled, "skipped": skipped}
        if target_engine.dialect.name == "postgresql":
            for table, _ in TABLES:
                dst.execute(text(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                                 f"COALESCE((SELECT max(id) FROM {table}), 1), (SELECT count(*) > 0 FROM {table}))"))
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sqlite", required=True, help="path to the old shariahease.db")
    ap.add_argument("--target", required=True, help="target database URL (postgresql://...)")
    ap.add_argument("--force", action="store_true", help="append even if the target already has users")
    args = ap.parse_args()

    path = Path(args.sqlite)
    if not path.is_file():
        print(f"SQLite file not found: {path}")
        return 2
    source = create_engine(f"sqlite:///file:{path.resolve().as_posix()}?mode=ro&uri=true")
    target = create_engine(normalise_url(args.target))
    counts = copy_database(source, target, args.force)
    for table, c in counts.items():
        extra = (f", {c['orphans_detached']} orphan links cleared" if c["orphans_detached"] else "") + \
                (f", {c['skipped']} guest/orphan messages skipped" if c["skipped"] else "")
        print(f"{table}: copied {c['copied']}{extra}")
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
