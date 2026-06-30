"""
Alpaca Paper Trading Executor

Submits buy/sell orders via the Alpaca REST API.
Uses direct HTTP requests (not the alpaca-trade-api SDK) for simplicity
and to avoid websocket version conflicts.
"""

import os
import requests
from typing import Optional
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', '.env'))

ALPACA_BASE_URL = "https://paper-api.alpaca.markets"
ALPACA_KEY = os.getenv("ALPACA_KEY")
ALPACA_SECRET = os.getenv("ALPACA_SECRET")

HEADERS = {
    "APCA-API-KEY-ID": ALPACA_KEY or "",
    "APCA-API-SECRET-KEY": ALPACA_SECRET or "",
    "Content-Type": "application/json",
}


def get_account() -> Optional[dict]:
    """Get account info from Alpaca."""
    try:
        resp = requests.get(f"{ALPACA_BASE_URL}/v2/account", headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        print(f"  [ALPACA] Account error: {resp.status_code} {resp.text[:200]}")
        return None
    except Exception as e:
        print(f"  [ALPACA] Account exception: {e}")
        return None


def get_portfolio_value() -> float:
    """Get current portfolio value."""
    account = get_account()
    if account:
        return float(account.get("portfolio_value", 0))
    return 0.0


def get_clock() -> Optional[dict]:
    """
    Get the Alpaca market clock.

    Returns a dict like {"is_open": bool, "next_open": str, "next_close": str},
    or None if the request fails.
    """
    try:
        resp = requests.get(f"{ALPACA_BASE_URL}/v2/clock", headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        print(f"  [ALPACA] Clock error: {resp.status_code} {resp.text[:200]}")
        return None
    except Exception as e:
        print(f"  [ALPACA] Clock exception: {e}")
        return None


def is_market_open() -> Optional[bool]:
    """Convenience wrapper: True/False if the market is open, None if unknown."""
    clock = get_clock()
    if clock is None:
        return None
    return bool(clock.get("is_open"))


def get_position(ticker: str) -> Optional[dict]:
    """Get current position for a ticker, or None if no position."""
    try:
        resp = requests.get(
            f"{ALPACA_BASE_URL}/v2/positions/{ticker}",
            headers=HEADERS, timeout=10
        )
        if resp.status_code == 200:
            return resp.json()
        return None  # 404 = no position
    except Exception:
        return None


def get_all_positions() -> list:
    """Get all open positions."""
    try:
        resp = requests.get(f"{ALPACA_BASE_URL}/v2/positions", headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        return []
    except Exception:
        return []


def get_order(order_id: str) -> Optional[dict]:
    """Fetch a single order by ID (used to reconcile actual fills)."""
    try:
        resp = requests.get(
            f"{ALPACA_BASE_URL}/v2/orders/{order_id}",
            headers=HEADERS, timeout=10
        )
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception:
        return None


def wait_for_fill(order_id: str, timeout_seconds: float = 4.0, poll_interval: float = 0.5) -> Optional[dict]:
    """
    Poll an order until it reaches a terminal state (filled / canceled /
    rejected / expired) or the timeout elapses. IOC orders usually settle in
    well under a second, but the initial POST response is 'pending_new'.

    Returns the latest order dict (which may still be non-terminal on timeout).
    """
    import time as _time
    terminal = {"filled", "canceled", "cancelled", "rejected", "expired", "done_for_day"}
    order = None
    deadline = _time.time() + timeout_seconds
    while _time.time() < deadline:
        order = get_order(order_id)
        if order and str(order.get("status", "")).lower() in terminal:
            return order
        _time.sleep(poll_interval)
    return order


def get_latest_price(ticker: str) -> Optional[float]:
    """Get latest trade price from Alpaca data API."""
    try:
        resp = requests.get(
            f"https://data.alpaca.markets/v2/stocks/{ticker}/trades/latest",
            headers=HEADERS, timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            return float(data.get("trade", {}).get("p", 0))
        return None
    except Exception:
        return None


def submit_order(
    ticker: str,
    qty: float,
    side: str,  # "buy" or "sell"
    order_type: str = "market",
    time_in_force: str = "ioc",  # Changed to IOC for biotech safety
    limit_price: float = None,
    notional: float = None,
) -> Optional[dict]:
    """
    Submit an order to Alpaca paper trading.

    SAFETY: Uses IOC (Immediate or Cancel) by default to prevent slippage
    on low-volume biotech stocks. If the order can't be filled immediately
    at a reasonable price, it cancels instead of sitting in the order book.

    FRACTIONAL/NOTIONAL: If `notional` (dollar amount) is provided, Alpaca
    requires a market order with TIF=day (IOC/FOK are not allowed for
    fractional/notional). This lets small accounts afford expensive names and
    fully deploy capital, at the cost of IOC slippage protection.

    Args:
        ticker: Stock symbol
        qty: Number of shares (can be fractional). Ignored if notional is set.
        side: "buy" or "sell"
        order_type: "market", "limit", etc.
        time_in_force: "ioc" (default), "fok", "day", "gtc"
        limit_price: Required for limit orders
        notional: Dollar amount for a fractional/notional order (buy side)

    Returns:
        Order response dict, or None on error.
    """
    if notional is not None:
        # Fractional/notional orders must be market + day.
        payload = {
            "symbol": ticker,
            "notional": str(round(float(notional), 2)),
            "side": side,
            "type": "market",
            "time_in_force": "day",
        }
        order_desc = f"${float(notional):.2f} notional"
    else:
        payload = {
            "symbol": ticker,
            "qty": str(qty),
            "side": side,
            "type": order_type,
            "time_in_force": time_in_force,
        }
        if limit_price is not None and order_type == "limit":
            payload["limit_price"] = str(limit_price)
        order_desc = f"{qty} shares (TIF={time_in_force})"

    try:
        resp = requests.post(
            f"{ALPACA_BASE_URL}/v2/orders",
            headers=HEADERS, json=payload, timeout=10
        )
        if resp.status_code in (200, 201):
            order = resp.json()
            status = order.get('status', 'unknown')
            filled_qty = order.get('filled_qty', '0')
            print(f"  [ALPACA] Order submitted: {side} {order_desc} {ticker} "
                  f"-> {order.get('id', 'N/A')[:8]}... "
                  f"status={status}, filled={filled_qty}")
            return order
        else:
            print(f"  [ALPACA] Order error: {resp.status_code} {resp.text[:300]}")
            return None
    except Exception as e:
        print(f"  [ALPACA] Order exception: {e}")
        return None


def submit_limit_order(
    ticker: str,
    qty: float,
    side: str,
    limit_price: float,
    time_in_force: str = "ioc",
) -> Optional[dict]:
    """
    Submit a limit order with price protection.
    
    Use this for volatile biotech stocks where market orders could slip badly.
    The limit_price acts as a "worst acceptable price" - the order will fill
    at limit_price or better, or cancel if the market has moved too far.
    """
    return submit_order(
        ticker=ticker,
        qty=qty,
        side=side,
        order_type="limit",
        time_in_force=time_in_force,
        limit_price=limit_price,
    )


def close_position(ticker: str) -> Optional[dict]:
    """Close an entire position in a ticker."""
    try:
        resp = requests.delete(
            f"{ALPACA_BASE_URL}/v2/positions/{ticker}",
            headers=HEADERS, timeout=10
        )
        if resp.status_code == 200:
            return resp.json()
        print(f"  [ALPACA] Close error: {resp.status_code} {resp.text[:200]}")
        return None
    except Exception as e:
        print(f"  [ALPACA] Close exception: {e}")
        return None
