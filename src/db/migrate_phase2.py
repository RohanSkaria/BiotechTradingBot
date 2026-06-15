"""
Phase 2 schema migration — adds catalyst-aware trade columns.

Run once against Neon (or SQLite fallback):
    python -m src.db.migrate_phase2
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.db.schema import get_connection, is_postgres

NEW_COLUMNS = [
    ("catalyst_date", "DATE"),
    ("decision_model", "TEXT"),
    ("hold_through", "BOOLEAN DEFAULT FALSE"),
    ("exit_reason", "TEXT"),
    ("closed_at", "TIMESTAMP"),
]


def migrate(db_path: str = None) -> None:
    conn = get_connection(db_path)
    try:
        if is_postgres():
            cur = conn.cursor()
            for col, col_type in NEW_COLUMNS:
                cur.execute(
                    f"ALTER TABLE trades ADD COLUMN IF NOT EXISTS {col} {col_type}"
                )
            conn.commit()
            print("✓ Phase 2 migration applied (PostgreSQL)")
        else:
            existing = {
                row[1]
                for row in conn.execute("PRAGMA table_info(trades)").fetchall()
            }
            for col, col_type in NEW_COLUMNS:
                if col not in existing:
                    # SQLite: simplified types
                    sqlite_type = "TEXT" if "DATE" in col_type or "TIMESTAMP" in col_type else (
                        "INTEGER" if "BOOLEAN" in col_type else "TEXT"
                    )
                    conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {sqlite_type}")
            conn.commit()
            print("✓ Phase 2 migration applied (SQLite)")
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
