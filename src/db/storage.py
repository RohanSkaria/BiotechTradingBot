"""
Database storage helpers for inserting and querying data.
Supports both PostgreSQL (Neon) and SQLite (local fallback).
"""

import hashlib
import json
import re
from datetime import datetime, date, timezone
from typing import Optional

from .schema import get_connection, is_postgres


def _placeholder():
    """Return the correct placeholder for the current database."""
    return "%s" if is_postgres() else "?"


def _execute(conn, query: str, params: tuple = None):
    """Execute a query with the appropriate cursor method."""
    if is_postgres():
        cur = conn.cursor()
        cur.execute(query, params)
        return cur
    else:
        return conn.execute(query, params or ())


def _get_lastrowid(cursor, conn):
    """Get the last inserted row ID."""
    if is_postgres():
        # For PostgreSQL, we use RETURNING id in the query
        result = cursor.fetchone()
        return result['id'] if result else None
    else:
        return cursor.lastrowid


def insert_news_event(
    ticker: str,
    headline: str,
    filing_type: str,
    accession_number: str,
    filing_url: str = "",
    raw_text: str = "",
    timestamp: str = None,
    source: str = "EDGAR",
    db_path: str = None,
) -> Optional[int]:
    """Insert a news event. Returns the row ID, or None if it's a duplicate accession."""
    conn = get_connection(db_path)
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    ph = _placeholder()
    
    try:
        if is_postgres():
            cur = conn.cursor()
            cur.execute(
                f"""INSERT INTO news_events (timestamp, source, ticker, headline, filing_type,
                   accession_number, filing_url, raw_text)
                   VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
                   ON CONFLICT (accession_number) DO NOTHING
                   RETURNING id""",
                (ts, source, ticker, headline, filing_type, accession_number, filing_url, raw_text)
            )
            result = cur.fetchone()
            conn.commit()
            return result['id'] if result else None
        else:
            cursor = conn.execute(
                """INSERT INTO news_events (timestamp, source, ticker, headline, filing_type,
                   accession_number, filing_url, raw_text)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (ts, source, ticker, headline, filing_type, accession_number, filing_url, raw_text)
            )
            news_id = cursor.lastrowid
            conn.commit()
            return news_id
    except Exception as e:
        error_str = str(e).lower()
        if "unique" in error_str or "duplicate" in error_str:
            return None  # duplicate accession number
        conn.rollback() if is_postgres() else None
        raise
    finally:
        conn.close()


def is_headline_seen(headline: str, db_path: str = None) -> bool:
    """Check if a headline has already been processed (by SHA-256 hash)."""
    conn = get_connection(db_path)
    h = hashlib.sha256(headline.encode()).hexdigest()
    ph = _placeholder()
    
    if is_postgres():
        cur = conn.cursor()
        cur.execute(f"SELECT 1 FROM headline_hashes WHERE hash = {ph}", (h,))
        row = cur.fetchone()
    else:
        row = conn.execute("SELECT 1 FROM headline_hashes WHERE hash = ?", (h,)).fetchone()
    
    conn.close()
    return row is not None


def insert_classification(
    news_id: int,
    category: str,
    sentiment: str,
    confidence: int,
    primary_ticker: str,
    affected_tickers: list,
    reasoning: str = "",
    model_used: str = "",
    token_count: int = 0,
    db_path: str = None,
) -> int:
    """Insert a classification result and mark the headline as seen (dedup). Returns the row ID."""
    conn = get_connection(db_path)
    ph = _placeholder()
    
    if is_postgres():
        cur = conn.cursor()
        cur.execute(
            f"""INSERT INTO classified_events
               (news_id, category, sentiment, confidence, primary_ticker,
                affected_tickers, reasoning, model_used, token_count)
               VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
               RETURNING id""",
            (news_id, category, sentiment, confidence, primary_ticker,
             json.dumps(affected_tickers), reasoning, model_used, token_count)
        )
        result = cur.fetchone()
        row_id = result['id']
        
        # Mark headline as classified (dedup)
        cur.execute(f"SELECT headline FROM news_events WHERE id = {ph}", (news_id,))
        headline_row = cur.fetchone()
        if headline_row:
            h = hashlib.sha256(headline_row['headline'].encode()).hexdigest()
            cur.execute(
                f"""INSERT INTO headline_hashes (hash, news_id) VALUES ({ph}, {ph})
                   ON CONFLICT (hash) DO NOTHING""",
                (h, news_id)
            )
    else:
        cursor = conn.execute(
            """INSERT INTO classified_events
               (news_id, category, sentiment, confidence, primary_ticker,
                affected_tickers, reasoning, model_used, token_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (news_id, category, sentiment, confidence, primary_ticker,
             json.dumps(affected_tickers), reasoning, model_used, token_count)
        )
        row_id = cursor.lastrowid

        headline_row = conn.execute(
            "SELECT headline FROM news_events WHERE id = ?", (news_id,)
        ).fetchone()
        if headline_row:
            h = hashlib.sha256(headline_row[0].encode()).hexdigest()
            conn.execute(
                "INSERT OR IGNORE INTO headline_hashes (hash, news_id) VALUES (?, ?)",
                (h, news_id)
            )

    conn.commit()
    conn.close()
    return row_id


def increment_llm_usage(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    db_path: str = None,
) -> None:
    """Increment the daily LLM usage counter."""
    conn = get_connection(db_path)
    today = date.today().isoformat()
    ph = _placeholder()
    
    if is_postgres():
        cur = conn.cursor()
        cur.execute(
            f"""INSERT INTO llm_usage (date, model, call_count, total_input_tokens, total_output_tokens)
               VALUES ({ph}, {ph}, 1, {ph}, {ph})
               ON CONFLICT(date, model) DO UPDATE SET
                 call_count = llm_usage.call_count + 1,
                 total_input_tokens = llm_usage.total_input_tokens + EXCLUDED.total_input_tokens,
                 total_output_tokens = llm_usage.total_output_tokens + EXCLUDED.total_output_tokens""",
            (today, model, input_tokens, output_tokens)
        )
    else:
        conn.execute(
            """INSERT INTO llm_usage (date, model, call_count, total_input_tokens, total_output_tokens)
               VALUES (?, ?, 1, ?, ?)
               ON CONFLICT(date, model) DO UPDATE SET
                 call_count = call_count + 1,
                 total_input_tokens = total_input_tokens + excluded.total_input_tokens,
                 total_output_tokens = total_output_tokens + excluded.total_output_tokens""",
            (today, model, input_tokens, output_tokens)
        )
    
    conn.commit()
    conn.close()


def get_daily_llm_calls(model: str = None, db_path: str = None) -> int:
    """Get total LLM calls made today (across all models, or for a specific model)."""
    conn = get_connection(db_path)
    today = date.today().isoformat()
    ph = _placeholder()
    
    if is_postgres():
        cur = conn.cursor()
        if model:
            cur.execute(
                f"SELECT COALESCE(SUM(call_count), 0) as total FROM llm_usage WHERE date = {ph} AND model = {ph}",
                (today, model)
            )
        else:
            cur.execute(
                f"SELECT COALESCE(SUM(call_count), 0) as total FROM llm_usage WHERE date = {ph}",
                (today,)
            )
        result = cur.fetchone()
        count = result['total'] if isinstance(result, dict) else result[0]
    else:
        if model:
            row = conn.execute(
                "SELECT COALESCE(SUM(call_count), 0) FROM llm_usage WHERE date = ? AND model = ?",
                (today, model)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COALESCE(SUM(call_count), 0) FROM llm_usage WHERE date = ?",
                (today,)
            ).fetchone()
        count = row[0]
    
    conn.close()
    return count


def insert_trade(
    ticker: str,
    side: str,
    qty: float,
    price: float = None,
    order_id: str = None,
    news_id: int = None,
    classified_id: int = None,
    stop_loss: float = None,
    take_profit: float = None,
    slippage_price_at_signal: float = None,
    catalyst_date: str = None,
    decision_model: str = None,
    hold_through: bool = False,
    db_path: str = None,
) -> int:
    """Insert a trade record. Returns the row ID."""
    conn = get_connection(db_path)
    ph = _placeholder()
    
    if is_postgres():
        cur = conn.cursor()
        cur.execute(
            f"""INSERT INTO trades
               (news_id, classified_id, ticker, side, qty, price, order_id,
                stop_loss, take_profit, slippage_price_at_signal,
                catalyst_date, decision_model, hold_through)
               VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
               RETURNING id""",
            (news_id, classified_id, ticker, side, qty, price, order_id,
             stop_loss, take_profit, slippage_price_at_signal,
             catalyst_date, decision_model, hold_through)
        )
        result = cur.fetchone()
        row_id = result['id']
    else:
        cursor = conn.execute(
            """INSERT INTO trades
               (news_id, classified_id, ticker, side, qty, price, order_id,
                stop_loss, take_profit, slippage_price_at_signal,
                catalyst_date, decision_model, hold_through)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (news_id, classified_id, ticker, side, qty, price, order_id,
             stop_loss, take_profit, slippage_price_at_signal,
             catalyst_date, decision_model, 1 if hold_through else 0)
        )
        row_id = cursor.lastrowid
    
    conn.commit()
    conn.close()
    return row_id


def update_trade_fill(
    trade_id: int,
    status: str = None,
    filled_qty: float = None,
    filled_avg_price: float = None,
    db_path: str = None,
) -> None:
    """
    Reconcile a logged trade with its actual Alpaca fill.

    Orders are inserted at submit time with the intended qty and signal price,
    but IOC orders frequently fill partially (or not at all). This updates the
    journal row so qty/price/status reflect what really happened.
    """
    sets = []
    params = []
    ph = _placeholder()

    if status is not None:
        sets.append(f"status = {ph}")
        params.append(status)
    if filled_qty is not None:
        sets.append(f"qty = {ph}")
        params.append(filled_qty)
    if filled_avg_price is not None:
        sets.append(f"price = {ph}")
        params.append(filled_avg_price)

    if not sets:
        return

    params.append(trade_id)
    query = f"UPDATE trades SET {', '.join(sets)} WHERE id = {ph}"

    conn = get_connection(db_path)
    try:
        if is_postgres():
            cur = conn.cursor()
            cur.execute(query, tuple(params))
        else:
            conn.execute(query, tuple(params))
        conn.commit()
    finally:
        conn.close()


def get_recent_news(limit: int = 20, ticker: str = None, db_path: str = None) -> list:
    """Get recent news events, optionally filtered by ticker."""
    conn = get_connection(db_path)
    ph = _placeholder()
    
    if is_postgres():
        cur = conn.cursor()
        if ticker:
            cur.execute(
                f"SELECT * FROM news_events WHERE ticker = {ph} ORDER BY timestamp DESC LIMIT {ph}",
                (ticker, limit)
            )
        else:
            cur.execute(
                f"SELECT * FROM news_events ORDER BY timestamp DESC LIMIT {ph}",
                (limit,)
            )
        rows = cur.fetchall()
    else:
        if ticker:
            rows = conn.execute(
                "SELECT * FROM news_events WHERE ticker = ? ORDER BY timestamp DESC LIMIT ?",
                (ticker, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM news_events ORDER BY timestamp DESC LIMIT ?",
                (limit,)
            ).fetchall()
    
    conn.close()
    return [dict(r) for r in rows]


def get_watchlist_from_db(db_path: str = None) -> list:
    """Get the watchlist from the database (PostgreSQL only)."""
    if not is_postgres():
        return []
    
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("SELECT * FROM weekly_watchlist WHERE active = TRUE ORDER BY priority DESC")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_latest_brief_signals(actionable_only: bool = False, db_path: str = None) -> list:
    """
    Get every signal from the most recent Dexter weekly brief.

    The weekly_briefings table lives in Neon Postgres (written by
    dexter/scripts/weekly-brief.ts). Each row carries a directional call
    (long/short/skip), a 0-100 conviction score, a thesis, catalysts (jsonb),
    and a high_conviction flag.

    Args:
        actionable_only: if True, drop "skip" signals and keep only long/short.

    Returns:
        List of dicts ordered by conviction (highest first). Empty list if not
        on Postgres or no briefs exist.
    """
    if not is_postgres():
        return []

    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT week_of, ticker, direction, conviction, thesis, catalysts, high_conviction
        FROM weekly_briefings
        WHERE week_of = (SELECT MAX(week_of) FROM weekly_briefings)
        ORDER BY conviction DESC, ticker ASC
        """
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    if actionable_only:
        rows = [r for r in rows if str(r.get("direction") or "").lower() in ("long", "short")]

    return rows


def get_watchlist_row(ticker: str, db_path: str = None) -> Optional[dict]:
    """Get a single active watchlist row by ticker."""
    for row in get_watchlist_from_db(db_path):
        if str(row.get("ticker", "")).upper() == ticker.upper():
            return row
    return None


def get_open_trades(db_path: str = None) -> list:
    """
    Trades that represent open positions (filled/partial, not yet closed).
    """
    conn = get_connection(db_path)
    if is_postgres():
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM trades
            WHERE closed_at IS NULL
              AND status IN ('filled', 'partially_filled', 'canceled')
              AND qty > 0
            ORDER BY created_at DESC
            """
        )
        rows = [dict(r) for r in cur.fetchall()]
    else:
        rows = [
            dict(r) for r in conn.execute(
                """
                SELECT * FROM trades
                WHERE closed_at IS NULL
                  AND status IN ('filled', 'partially_filled', 'canceled')
                  AND qty > 0
                ORDER BY created_at DESC
                """
            ).fetchall()
        ]
    conn.close()
    return rows


def get_latest_open_trade(ticker: str, db_path: str = None) -> Optional[dict]:
    """Most recent open trade journal row for a ticker."""
    open_trades = [t for t in get_open_trades(db_path) if t.get("ticker") == ticker.upper()]
    return open_trades[0] if open_trades else None


def update_high_water(trade_id: int, price: float, db_path: str = None) -> None:
    """Raise the stored high-water price for a trade (used for trailing stops)."""
    conn = get_connection(db_path)
    ph = _placeholder()
    query = (
        f"UPDATE trades SET high_water_price = {ph} "
        f"WHERE id = {ph} AND (high_water_price IS NULL OR high_water_price < {ph})"
    )
    try:
        if is_postgres():
            cur = conn.cursor()
            cur.execute(query, (price, trade_id, price))
        else:
            conn.execute(query, (price, trade_id, price))
        conn.commit()
    finally:
        conn.close()


def close_trade_record(
    trade_id: int,
    exit_reason: str,
    pnl: float = None,
    status: str = "closed",
    db_path: str = None,
) -> None:
    """Mark a trade as closed in the journal."""
    conn = get_connection(db_path)
    ph = _placeholder()
    now = datetime.now(timezone.utc).isoformat()
    params = (status, exit_reason, now, pnl, trade_id)
    query = f"""
        UPDATE trades
        SET status = {ph}, exit_reason = {ph}, closed_at = {ph}, pnl = {ph}
        WHERE id = {ph}
    """
    if is_postgres():
        cur = conn.cursor()
        cur.execute(query, params)
    else:
        conn.execute(query, params)
    conn.commit()
    conn.close()
