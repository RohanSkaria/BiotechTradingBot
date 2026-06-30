"""
Position Manager — catalyst-aware exit engine.

Exit rules (research-backed; see src/backtest/exit_study.py + the lit review):
  - Hard stop-loss (catastrophic floor; tiers in risk_manager)
  - Trailing stop off the high-water mark — lets winners run while protecting
    gains, which fits the empirical finding that post-catalyst returns FADE
    gradually (no cliff) so you exit as a name rolls over, not at a guessed top
  - Optional fixed take-profit (default OFF so winners aren't capped)
  - Pre-catalyst exit for UPCOMING binaries (gap risk)
  - Optional post-catalyst time exit (the study shows the edge dies ~3-5d out)
  - Daily loss limit (via risk_manager)

Run manually:
    python -m src.trading.position_manager --dry-run
"""

import os
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.config.strategy import load_strategy
from src.db.storage import (
    get_latest_open_trade,
    close_trade_record,
    get_watchlist_row,
    update_high_water,
)
from src.trading.executor import (
    get_all_positions,
    get_latest_price,
    close_position,
    is_market_open,
)
from src.trading.risk_manager import should_stop_trading
from src.analysis.trade_decider import days_until, extract_catalyst_date
from src.alerts.discord import send_exit_alert, send_system_alert


def _should_exit(
    side: str,
    current_price: float,
    entry: float,
    stop_loss: float,
    take_profit: float,
    high_water: float,
    catalyst_date,
    hold_through: bool,
    strategy: dict,
) -> tuple:
    """
    Returns (should_exit: bool, reason: str). First matching rule wins.
    """
    if not current_price or current_price <= 0:
        return False, ""

    is_long = side in ("buy", "long")

    # 1. Hard stop-loss (catastrophic floor)
    if strategy.get("use_stop_loss", True) and stop_loss:
        if is_long and current_price <= stop_loss:
            return True, f"stop_loss (${current_price:.2f} <= ${stop_loss:.2f})"
        if not is_long and current_price >= stop_loss:
            return True, f"stop_loss (${current_price:.2f} >= ${stop_loss:.2f})"

    # 2. Trailing stop off the high-water mark (long-side; protects gains, lets winners run)
    if strategy.get("trailing_stop_enabled", True) and is_long and high_water and high_water > 0:
        trail_pct = float(strategy.get("trailing_stop_pct", 0.12))
        trail_price = round(high_water * (1 - trail_pct), 2)
        # Only meaningful once the name has appreciated above the hard stop.
        if trail_price > (stop_loss or 0) and current_price <= trail_price:
            return True, (f"trailing_stop (-{trail_pct*100:.0f}% from high "
                          f"${high_water:.2f}: ${current_price:.2f} <= ${trail_price:.2f})")

    # 3. Optional fixed take-profit (default OFF)
    if strategy.get("use_take_profit", False) and take_profit:
        if is_long and current_price >= take_profit:
            return True, f"take_profit (${current_price:.2f} >= ${take_profit:.2f})"
        if not is_long and current_price <= take_profit:
            return True, f"take_profit (${current_price:.2f} <= ${take_profit:.2f})"

    # Catalyst-relative timing
    if catalyst_date:
        days = days_until(str(catalyst_date)[:10])
        if days is not None:
            # 4. Pre-catalyst exit — only for UPCOMING binaries (gap risk)
            if not hold_through:
                exit_days = strategy.get("pre_catalyst_exit_days", 1)
                if 0 <= days <= exit_days:
                    return True, f"pre_catalyst_exit ({days}d to catalyst {catalyst_date})"
            # 5. Optional post-catalyst time exit (edge fades ~3-5d out)
            pced = strategy.get("post_catalyst_exit_days")
            if pced is not None and days < 0 and abs(days) >= int(pced):
                return True, f"post_catalyst_exit ({abs(days)}d after catalyst {catalyst_date})"

    return False, ""


def manage_positions(dry_run: bool = False, send_discord: bool = True) -> list:
    """
    Scan open positions and exit when guardrails trigger.

    Returns list of action dicts.
    """
    strategy = load_strategy()
    actions = []

    if is_market_open() is False and not dry_run:
        return actions

    stop = should_stop_trading()
    if stop["stop"]:
        print(f"  [POSITIONS] Paused — {stop['reason']}")
        return actions

    positions = get_all_positions()
    if not positions:
        return actions

    print(f"  [POSITIONS] Monitoring {len(positions)} open position(s)...")

    for pos in positions:
        ticker = pos.get("symbol", "").upper()
        current_price = float(pos.get("current_price") or get_latest_price(ticker) or 0)
        qty = float(pos.get("qty") or 0)
        side = pos.get("side", "long")
        entry = float(pos.get("avg_entry_price") or 0)
        unrealized = float(pos.get("unrealized_pl") or 0)

        trade = get_latest_open_trade(ticker)
        stop_loss = float(trade.get("stop_loss") or 0) if trade else 0
        take_profit = float(trade.get("take_profit") or 0) if trade else 0
        catalyst_date = trade.get("catalyst_date") if trade else None
        if trade and not catalyst_date:
            catalyst_date = extract_catalyst_date({}, get_watchlist_row(ticker))
        hold_through = bool(trade.get("hold_through")) if trade else False
        trade_side = trade.get("side", "buy") if trade else "buy"

        # High-water mark for trailing stop: raise to the highest price we've seen.
        stored_hw = float(trade.get("high_water_price") or 0) if trade else 0
        high_water = max(stored_hw, current_price, entry)
        if trade and high_water > stored_hw and not dry_run:
            update_high_water(trade["id"], high_water)

        should_exit, reason = _should_exit(
            side=trade_side,
            current_price=current_price,
            entry=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            high_water=high_water,
            catalyst_date=catalyst_date,
            hold_through=hold_through,
            strategy=strategy,
        )

        if not should_exit:
            continue

        print(f"  [POSITIONS] EXIT {ticker}: {reason} (qty={qty}, uPL=${unrealized:+.2f})")

        if dry_run:
            actions.append({"ticker": ticker, "status": "dry_run", "reason": reason})
            continue

        result = close_position(ticker)
        if not result:
            actions.append({"ticker": ticker, "status": "close_failed", "reason": reason})
            continue

        if trade:
            close_trade_record(
                trade_id=trade["id"],
                exit_reason=reason,
                pnl=unrealized,
                status="closed",
            )

        if send_discord:
            send_exit_alert(
                ticker=ticker,
                side=trade_side,
                qty=int(qty),
                entry_price=entry,
                exit_price=current_price,
                pnl=unrealized,
                reason=reason,
            )

        actions.append({
            "ticker": ticker, "status": "closed", "reason": reason, "pnl": unrealized,
        })

    if actions and send_discord and not dry_run:
        closed = [a for a in actions if a["status"] == "closed"]
        if closed:
            lines = "\n".join(
                f"- {a['ticker']}: {a['reason']} (P&L ${a['pnl']:+.2f})" for a in closed
            )
            send_system_alert("Position Exits", f"{len(closed)} position(s) closed:\n{lines}")

    return actions


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Monitor and exit open positions.")
    parser.add_argument("--dry-run", action="store_true", help="Preview exits without closing")
    parser.add_argument("--no-discord", action="store_true")
    args = parser.parse_args()

    print("=" * 60)
    print("POSITION MANAGER")
    print("=" * 60)
    manage_positions(dry_run=args.dry_run, send_discord=not args.no_discord)
