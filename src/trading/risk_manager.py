"""
Risk Manager

Enforces position sizing, per-trade limits, stop-loss / take-profit,
and maximum daily loss constraints.

Stop-loss is dynamically calculated based on market cap tier and volatility:
- Large cap, low volatility: 8% (conservative)
- Large cap, medium volatility: 10% (standard)
- Mid/small cap, high volatility: 15% (allows for biotech swings)
"""

import os
import sys
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from src.trading.executor import get_portfolio_value, get_position, get_all_positions
from src.config.strategy import load_strategy

_strategy = load_strategy()
MAX_POSITION_PCT = _strategy.get("max_position_pct", 0.05)
MAX_DAILY_LOSS_PCT = _strategy.get("max_daily_loss_pct", 0.02)
MAX_OPEN_POSITIONS = _strategy.get("max_open_positions", 5)
MAX_TOTAL_EXPOSURE_PCT = _strategy.get("max_total_exposure_pct", 0.25)

# Dynamic stop-loss by market cap tier + volatility
# Key format: "{market_cap_tier}_{volatility}"
STOP_LOSS_TIERS = {
    "large_low": 0.08,      # 8% - Large cap, low volatility (LLY, AMGN, GILD)
    "large_medium": 0.10,   # 10% - Large cap, medium volatility (VRTX, REGN)
    "large_high": 0.12,     # 12% - Large cap, high volatility (rare)
    "mid_low": 0.10,        # 10% - Mid cap, low volatility
    "mid_medium": 0.12,     # 12% - Mid cap, medium volatility
    "mid_high": 0.15,       # 15% - Mid cap, high volatility (CRSP)
    "small_low": 0.12,      # 12% - Small cap, low volatility
    "small_medium": 0.15,   # 15% - Small cap, medium volatility
    "small_high": 0.18,     # 18% - Small cap, high volatility (very speculative)
    "default": 0.10,        # 10% - Fallback
}

# Take-profit multiplier based on stop-loss (risk:reward ratio)
# More conservative stop-loss -> higher take-profit target
TAKE_PROFIT_MULTIPLIER = 2.0  # 2:1 reward:risk ratio

WATCHLIST_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'watchlist.yaml')


def load_watchlist() -> dict:
    """Load the watchlist YAML config."""
    try:
        with open(WATCHLIST_PATH, 'r') as f:
            return yaml.safe_load(f)
    except Exception:
        return {}


def _get_ticker_info_from_db(ticker: str) -> dict:
    """
    Look up ticker metadata in the weekly_watchlist DB table.

    Tickers discovered by the Dexter brief (e.g. VRDN, ARQT, IONS) are not in
    config/watchlist.yaml, so without this fallback they'd always get generic
    mid/medium defaults and the wrong stop-loss tier.
    """
    try:
        from src.db.storage import get_watchlist_from_db
        for row in get_watchlist_from_db():
            if str(row.get("ticker", "")).strip().upper() == ticker.upper():
                return {
                    "market_cap_tier": (row.get("market_cap_tier") or "mid").strip(),
                    "volatility": (row.get("volatility") or "medium").strip(),
                    "focus": (row.get("focus") or row.get("expected_catalyst") or ""),
                }
    except Exception:
        pass
    return {}


def get_ticker_info(ticker: str) -> dict:
    """Get market cap tier and volatility info for a ticker from watchlist."""
    watchlist = load_watchlist()
    
    # New format: watchlists.core_biotech is a list
    if "watchlists" in watchlist:
        for item in watchlist.get("watchlists", {}).get("core_biotech", []):
            if item.get("ticker") == ticker:
                return {
                    "market_cap_tier": item.get("market_cap_tier", "mid"),
                    "volatility": item.get("volatility", "medium"),
                    "focus": item.get("focus", ""),
                }
    
    # Old format: tickers dict
    elif "tickers" in watchlist:
        if ticker in watchlist.get("tickers", {}):
            info = watchlist["tickers"][ticker]
            return {
                "market_cap_tier": info.get("market_cap_tier", "mid"),
                "volatility": info.get("volatility", "medium"),
                "focus": info.get("focus", info.get("key_catalyst", "")),
            }
    
    # Fallback: tickers discovered via the Dexter brief live only in the DB
    db_info = _get_ticker_info_from_db(ticker)
    if db_info:
        return db_info

    return {"market_cap_tier": "mid", "volatility": "medium", "focus": ""}


def get_stop_loss_pct(ticker: str) -> float:
    """
    Calculate dynamic stop-loss percentage based on ticker's market cap and volatility.
    
    Returns:
        Stop-loss as decimal (e.g., 0.10 for 10%)
    """
    info = get_ticker_info(ticker)
    tier = info.get("market_cap_tier", "mid")
    vol = info.get("volatility", "medium")
    
    key = f"{tier}_{vol}"
    stop_loss = STOP_LOSS_TIERS.get(key, STOP_LOSS_TIERS["default"])
    
    return stop_loss


def get_take_profit_pct(stop_loss_pct: float) -> float:
    """
    Calculate take-profit based on stop-loss (maintains risk:reward ratio).
    """
    return stop_loss_pct * TAKE_PROFIT_MULTIPLIER


def _total_exposure_pct(portfolio_value: float, positions: list) -> float:
    if portfolio_value <= 0:
        return 0.0
    exposure = sum(abs(float(p.get("market_value", 0))) for p in positions)
    return exposure / portfolio_value


def calculate_position_size(
    ticker: str,
    price: float,
    sentiment: str = "Neutral",
    confidence: int = 50,
    size_tier_multiplier: float = 1.0,
    side: str = "buy",
) -> dict:
    """
    Calculate the position size for a trade based on risk parameters.

    Returns dict with:
        - qty: number of shares
        - dollar_amount: dollar value of position
        - allowed: whether the trade is allowed
        - reason: explanation if not allowed
        - stop_loss_pct: calculated stop-loss percentage
        - stop_loss_price: calculated stop-loss price
        - take_profit_price: calculated take-profit price
    """
    portfolio_value = get_portfolio_value()
    if portfolio_value <= 0:
        return {"qty": 0, "dollar_amount": 0, "allowed": False, "reason": "Cannot read portfolio value"}

    if price <= 0:
        return {"qty": 0, "dollar_amount": 0, "allowed": False, "reason": "Invalid price"}

    # Check max open positions
    positions = get_all_positions()
    if len(positions) >= MAX_OPEN_POSITIONS:
        return {"qty": 0, "dollar_amount": 0, "allowed": False,
                "reason": f"Max open positions reached ({MAX_OPEN_POSITIONS})"}

    # Total portfolio exposure guardrail
    exposure_pct = _total_exposure_pct(portfolio_value, positions)
    if exposure_pct >= MAX_TOTAL_EXPOSURE_PCT:
        return {"qty": 0, "dollar_amount": 0, "allowed": False,
                "reason": f"Max total exposure reached ({exposure_pct*100:.1f}% >= {MAX_TOTAL_EXPOSURE_PCT*100:.0f}%)"}

    # Check if we already have a position in this ticker
    existing = get_position(ticker)
    if existing:
        return {"qty": 0, "dollar_amount": 0, "allowed": False,
                "reason": f"Already have position in {ticker}"}

    # Scale position size by confidence
    # High confidence (80-100) -> full 5%
    # Medium confidence (50-79) -> 3%
    # Low confidence (30-49) -> 1.5%
    if confidence >= 80:
        pct = MAX_POSITION_PCT
    elif confidence >= 50:
        pct = MAX_POSITION_PCT * 0.6
    else:
        pct = MAX_POSITION_PCT * 0.3

    # Further scale by sentiment strength
    if sentiment in ("Strong Positive", "Strong Negative"):
        pct *= 1.0  # full allocation
    elif sentiment in ("Weak Positive", "Weak Negative"):
        pct *= 0.6
    else:
        pct *= 0.3  # minimal for neutral

    pct *= max(0.0, min(1.0, size_tier_multiplier))

    dollar_amount = portfolio_value * pct
    qty = int(dollar_amount / price)  # whole shares only for simplicity

    if qty <= 0:
        return {"qty": 0, "dollar_amount": 0, "allowed": False,
                "reason": "Position too small (< 1 share)"}

    # Calculate dynamic stop-loss and take-profit
    stop_loss_pct = get_stop_loss_pct(ticker)
    take_profit_pct = get_take_profit_pct(stop_loss_pct)
    
    ticker_info = get_ticker_info(ticker)

    if side == "sell":
        stop_loss_price = round(price * (1 + stop_loss_pct), 2)
        take_profit_price = round(price * (1 - take_profit_pct), 2)
    else:
        stop_loss_price = round(price * (1 - stop_loss_pct), 2)
        take_profit_price = round(price * (1 + take_profit_pct), 2)

    return {
        "qty": qty,
        "dollar_amount": round(qty * price, 2),
        "allowed": True,
        "reason": f"OK: {pct*100:.1f}% of ${portfolio_value:,.0f} portfolio",
        "stop_loss_pct": stop_loss_pct,
        "stop_loss_price": stop_loss_price,
        "take_profit_pct": take_profit_pct,
        "take_profit_price": take_profit_price,
        "market_cap_tier": ticker_info.get("market_cap_tier", "unknown"),
        "volatility": ticker_info.get("volatility", "unknown"),
    }


def get_trade_side(sentiment: str) -> str:
    """
    Determine buy/sell side from sentiment.
    Positive -> buy, Negative -> sell (short).
    """
    if sentiment in ("Strong Positive", "Weak Positive"):
        return "buy"
    elif sentiment in ("Strong Negative", "Weak Negative"):
        return "sell"
    else:
        return "skip"


def should_stop_trading(db_path: str = None) -> dict:
    """
    Check if we should stop trading due to daily loss limit.
    Returns dict with 'stop' bool and 'reason'.
    """
    # For paper trading, we check unrealized P&L from positions
    positions = get_all_positions()
    total_unrealized = sum(float(p.get("unrealized_pl", 0)) for p in positions)
    portfolio_value = get_portfolio_value()

    if portfolio_value > 0:
        loss_pct = total_unrealized / portfolio_value
        if loss_pct < -MAX_DAILY_LOSS_PCT:
            return {
                "stop": True,
                "reason": f"Daily loss limit hit: {loss_pct*100:.2f}% (limit: {MAX_DAILY_LOSS_PCT*100:.1f}%)",
                "unrealized_pl": total_unrealized,
            }

    return {
        "stop": False,
        "reason": "OK",
        "unrealized_pl": total_unrealized,
    }
