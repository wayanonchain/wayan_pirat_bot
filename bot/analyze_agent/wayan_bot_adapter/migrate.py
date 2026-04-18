"""
Apply the accumulation-module migration to a WAYNE_PIRATE bot.db.

Usage (from inside the WAYNE_PIRATE project after copying this package):
    python -m bot.analyze_agent.wayan_bot_adapter.migrate

Or standalone:
    python migrate.py /path/to/bot.db

Safe to run repeatedly — the SQL uses IF NOT EXISTS everywhere.
"""
import sqlite3
import sys
from pathlib import Path


def apply_migration(db_path: str) -> None:
    sql_file = Path(__file__).parent / "migrations.sql"
    sql = sql_file.read_text()
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(sql)
        conn.commit()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'accumulation_%'"
        ).fetchall()
        print(f"✅ Migration applied. Present tables: {[t[0] for t in tables]}")
    finally:
        conn.close()


def _resolve_db_path(argv: list[str]) -> str:
    if len(argv) >= 2:
        return argv[1]
    # Default: assume we're running inside WAYNE_PIRATE repo layout
    candidates = [
        Path("data/bot.db"),
        Path(__file__).resolve().parents[3] / "data" / "bot.db",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    print("Could not find bot.db. Pass path explicitly: python migrate.py <bot.db>")
    sys.exit(1)


if __name__ == "__main__":
    path = _resolve_db_path(sys.argv)
    print(f"Applying migration to {path}")
    apply_migration(path)
