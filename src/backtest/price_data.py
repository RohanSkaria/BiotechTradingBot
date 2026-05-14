"""
Historical Price Data Fetcher

Uses yfinance to pull price data around known catalyst dates.
Calculates returns at various time windows (1h, 4h, 1d) after events.
"""

import yfinance as yf
from datetime import datetime, timedelta
from typing import Optional


def get_price_around_event(
    ticker: str,
    event_date: str,
    days_before: int = 2,
    days_after: int = 3,
) -> Optional[dict]:
    """
    Fetch price data around an event date.

    Args:
        ticker: Stock ticker (e.g., "LLY")
        event_date: Date string "YYYY-MM-DD"
        days_before: Trading days before event to fetch
        days_after: Trading days after event to fetch

    Returns:
        Dict with price data and computed returns, or None on error.
    """
    try:
        event_dt = datetime.strptime(event_date, "%Y-%m-%d")
        start = event_dt - timedelta(days=days_before + 5)  # buffer for weekends
        end = event_dt + timedelta(days=days_after + 5)

        stock = yf.Ticker(ticker)
        hist = stock.history(start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"))

        if hist.empty:
            return None

        # Find the closest trading day to the event date
        event_str = event_date
        hist.index = hist.index.tz_localize(None)

        # Get price on or just before event
        pre_event = hist[hist.index <= event_dt]
        post_event = hist[hist.index > event_dt]

        if pre_event.empty or post_event.empty:
            return None

        price_at_event = pre_event.iloc[-1]["Close"]
        price_day_before = pre_event.iloc[-2]["Close"] if len(pre_event) >= 2 else price_at_event

        # Returns at various windows
        returns = {}
        for i, label in enumerate(["1d", "2d", "3d"]):
            if i < len(post_event):
                future_price = post_event.iloc[i]["Close"]
                ret = (future_price - price_at_event) / price_at_event * 100
                returns[label] = round(ret, 2)

        return {
            "ticker": ticker,
            "event_date": event_date,
            "price_at_event": round(price_at_event, 2),
            "price_day_before": round(price_day_before, 2),
            "pre_event_return": round(
                (price_at_event - price_day_before) / price_day_before * 100, 2
            ),
            "returns": returns,
            "volume_at_event": int(pre_event.iloc[-1].get("Volume", 0)),
        }

    except Exception as e:
        print(f"  [PRICE] Error fetching {ticker} around {event_date}: {e}")
        return None


def get_current_price(ticker: str) -> Optional[float]:
    """Get the current/latest price for a ticker."""
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1d")
        if not hist.empty:
            return round(hist.iloc[-1]["Close"], 2)
        return None
    except Exception:
        return None


if __name__ == "__main__":
    # Test with a known event: LLY 8-K filed 2026-02-04
    result = get_price_around_event("LLY", "2026-02-04")
    if result:
        print(f"Ticker: {result['ticker']}")
        print(f"Event Date: {result['event_date']}")
        print(f"Price at Event: ${result['price_at_event']}")
        print(f"Pre-event Return: {result['pre_event_return']}%")
        print(f"Post-event Returns: {result['returns']}")
        print(f"Volume: {result['volume_at_event']:,}")
