"""
PostgreSQL database connection for the biotech trading bot (Neon).
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

# Keep SQLite path for local fallback/testing
DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'biotech_bot.db')


def get_connection(db_path: str = None):
    """
    Get a database connection.
    - If DATABASE_URL is set, connects to Neon PostgreSQL
    - Otherwise falls back to local SQLite (for testing)
    """
    if DATABASE_URL:
        # PostgreSQL (Neon)
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        return conn
    else:
        # Fallback to SQLite for local testing
        import sqlite3
        path = db_path or DB_PATH
        os.makedirs(os.path.dirname(path), exist_ok=True)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn


def is_postgres() -> bool:
    """Check if we're using PostgreSQL."""
    return DATABASE_URL is not None


def init_db(db_path: str = None) -> None:
    """
    Initialize database tables.
    For PostgreSQL (Neon), run the SQL script in the Neon console.
    For SQLite, creates tables locally.
    """
    if DATABASE_URL:
        print("Using Neon PostgreSQL. Run schema SQL in Neon console if tables don't exist.")
        # Test connection
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) as cnt FROM weekly_watchlist")
            result = cur.fetchone()
            count = result['cnt'] if isinstance(result, dict) else result[0]
            print(f"✓ Connected to Neon. Watchlist has {count} tickers.")
            conn.close()
        except Exception as e:
            print(f"✗ Neon connection error: {e}")
        return

    # SQLite fallback - create tables locally
    import sqlite3
    conn = get_connection(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS news_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'EDGAR',
            ticker TEXT NOT NULL,
            headline TEXT NOT NULL,
            filing_type TEXT,
            accession_number TEXT UNIQUE,
            filing_url TEXT,
            raw_text TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS classified_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            news_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            sentiment TEXT NOT NULL,
            confidence INTEGER NOT NULL,
            primary_ticker TEXT NOT NULL,
            affected_tickers TEXT,
            reasoning TEXT,
            model_used TEXT,
            token_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (news_id) REFERENCES news_events(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS llm_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            model TEXT NOT NULL,
            call_count INTEGER DEFAULT 0,
            total_input_tokens INTEGER DEFAULT 0,
            total_output_tokens INTEGER DEFAULT 0,
            UNIQUE(date, model)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS headline_hashes (
            hash TEXT PRIMARY KEY,
            news_id INTEGER NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (news_id) REFERENCES news_events(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            news_id INTEGER,
            classified_id INTEGER,
            ticker TEXT NOT NULL,
            side TEXT NOT NULL,
            qty REAL NOT NULL,
            price REAL,
            order_id TEXT,
            status TEXT DEFAULT 'pending',
            stop_loss REAL,
            take_profit REAL,
            slippage_price_at_signal REAL,
            slippage_price_after_30s REAL,
            pnl REAL,
            catalyst_date TEXT,
            decision_model TEXT,
            hold_through INTEGER DEFAULT 0,
            exit_reason TEXT,
            closed_at TEXT,
            high_water_price REAL,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (news_id) REFERENCES news_events(id),
            FOREIGN KEY (classified_id) REFERENCES classified_events(id)
        )
    """)

    conn.commit()
    db_file = db_path or DB_PATH
    conn.close()
    print(f"SQLite database initialized at {os.path.abspath(db_file)}")


if __name__ == "__main__":
    init_db()
