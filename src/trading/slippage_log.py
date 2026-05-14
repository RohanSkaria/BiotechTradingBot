"""
Slippage Logger

Logs the quoted price at signal time and the price 30 seconds later
to estimate real-world slippage for each trade.
"""

import time
import threading
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from src.trading.executor import get_latest_price
from src.db.schema import get_connection


def log_slippage_async(
    trade_id: int,
    ticker: str,
    price_at_signal: float,
    delay_seconds: int = 30,
    db_path: str = None,
) -> None:
    """
    Start a background thread that waits `delay_seconds`, then fetches
    the current price and logs the slippage to the trades table.
    """
    def _log():
        time.sleep(delay_seconds)
        price_after = get_latest_price(ticker)
        if price_after is None:
            print(f"  [SLIPPAGE] Could not fetch price for {ticker} after {delay_seconds}s")
            return

        slippage = price_after - price_at_signal
        slippage_pct = (slippage / price_at_signal) * 100 if price_at_signal > 0 else 0

        # Update the trade record
        conn = get_connection(db_path)
        conn.execute(
            "UPDATE trades SET slippage_price_after_30s = ? WHERE id = ?",
            (price_after, trade_id)
        )
        conn.commit()
        conn.close()

        print(f"  [SLIPPAGE] {ticker}: signal=${price_at_signal:.2f} -> "
              f"after {delay_seconds}s=${price_after:.2f} "
              f"(slippage: {slippage_pct:+.2f}%)")

    thread = threading.Thread(target=_log, daemon=True)
    thread.start()
