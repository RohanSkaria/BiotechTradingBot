"""
Scale-to-target rebalancer.

The one-shot brief trader enters a name once and never adds. This module sizes
every eligible high-conviction name to a target % of the portfolio derived from
conviction, then tops up toward that target each run (ratchet up; trimming is
opt-in). This is what lets a rising conviction on a name you already hold
actually increase the position.

Flow per run (Mon-Fri after the brief):
  1. Latest Claude brief signals (research)
  2. Keep high-conviction longs whose catalyst is still within entry_window_days
  3. Free Gemini decider ($0) gates each candidate (veto + metadata)
  4. target_$ = conviction-based % of portfolio (capped by per-name + total caps)
  5. delta = target_$ - current_position_$  -> BUY the delta (or trim if enabled)

Usage:
    python -m src.trading.rebalancer --dry-run
    python -m src.trading.rebalancer
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.config.strategy import load_strategy
from src.db.storage import get_watchlist_row, insert_trade, update_trade_fill
from src.analysis.trade_decider import decide_trade, size_tier_multiplier
from src.trading.brief_trader import select_signals, _conviction_to_sentiment, DIRECTION_TO_SIDE
from src.db.storage import get_latest_brief_signals
from src.trading.executor import (
    get_latest_price,
    get_all_positions,
    get_portfolio_value,
    get_clock,
    submit_order,
    wait_for_fill,
)
from src.trading.risk_manager import (
    should_stop_trading,
    get_stop_loss_pct,
    get_take_profit_pct,
)
from src.trading.slippage_log import log_slippage_async
from src.alerts.discord import send_trade_alert, send_system_alert


def target_pct_for_conviction(conviction: int, strategy: dict = None) -> float:
    """
    Map conviction -> target weight as a fraction of the portfolio.

    Linear: rebalance_min_conviction -> target_floor_pct, 100 -> max_position_pct.
    Below the minimum conviction, target is 0 (not held by the strategy).
    """
    strategy = strategy or load_strategy()
    min_conv = strategy.get("rebalance_min_conviction", 70)
    floor = strategy.get("target_floor_pct", 0.02)
    cap = strategy.get("max_position_pct", 0.05)

    c = int(conviction or 0)
    if c < min_conv:
        return 0.0
    span = max(1, 100 - min_conv)
    frac = min(1.0, (c - min_conv) / span)
    return floor + frac * (cap - floor)


def _positions_by_symbol() -> dict:
    out = {}
    for p in get_all_positions():
        out[p.get("symbol", "").upper()] = p
    return out


def rebalance_from_brief(
    dry_run: bool = False,
    send_discord: bool = True,
    ignore_market_hours: bool = False,
) -> list:
    """
    Size every eligible high-conviction name to its conviction-based target and
    top up toward it. Returns a list of per-ticker action dicts.
    """
    strategy = load_strategy()
    if not strategy.get("rebalance_enabled", True):
        print("  [REBAL] Rebalancing disabled in strategy.yaml")
        return []

    print("=" * 60)
    print("SCALE-TO-TARGET REBALANCER")
    print("=" * 60)

    clock = get_clock()
    is_open = clock.get("is_open") if clock else None
    if clock is not None:
        print(f"  [CLOCK] Market open: {is_open}")
    if is_open is False and not dry_run and not ignore_market_hours:
        print("  [REBAL] Market closed -- skipping.")
        return []

    stop = should_stop_trading()
    if stop["stop"]:
        print(f"  [RISK] {stop['reason']}")
        if send_discord:
            send_system_alert("Rebalancer Paused", stop["reason"])
        return []

    signals = get_latest_brief_signals()
    if not signals:
        print("  [REBAL] No brief signals found.")
        return []

    week_of = signals[0].get("week_of")
    # Same eligibility as entries: high-conviction, actionable, catalyst in window.
    picks = select_signals(
        signals,
        high_conviction_only=True,
        min_conviction=strategy.get("rebalance_min_conviction", 70),
    )
    print(f"  [REBAL] Week of {week_of}: {len(picks)} eligible name(s) in catalyst window")
    if dry_run:
        print("  [REBAL] DRY RUN -- no orders will be submitted")

    portfolio = get_portfolio_value()
    if portfolio <= 0:
        print("  [REBAL] Cannot read portfolio value.")
        return []
    print(f"  [REBAL] Portfolio: ${portfolio:,.2f}")

    positions = _positions_by_symbol()
    running_exposure = sum(abs(float(p.get("market_value", 0))) for p in positions.values())
    max_total = strategy.get("max_total_exposure_pct", 0.25) * portfolio
    max_positions = strategy.get("max_open_positions", 5)
    min_delta = max(
        strategy.get("rebalance_min_delta_pct", 0.01) * portfolio,
        strategy.get("min_order_notional", 1.0),
    )
    allow_fractional = bool(strategy.get("allow_fractional", False))
    allow_trim = bool(strategy.get("rebalance_allow_trim", False))

    results = []
    for s in picks:
        ticker = str(s["ticker"]).upper()
        direction = str(s.get("direction") or "").lower()
        if direction != "long":
            # Scale-to-target add logic is long-only for now (watchlist is long-biased).
            results.append({"ticker": ticker, "status": "skip_non_long"})
            continue

        price = get_latest_price(ticker)
        if not price:
            results.append({"ticker": ticker, "status": "no_price"})
            continue

        watchlist_row = get_watchlist_row(ticker)
        decision = decide_trade(s, price, watchlist_row)
        if not decision:
            results.append({"ticker": ticker, "status": "decider_failed"})
            continue
        if not decision.get("act"):
            print(f"  [REBAL] {ticker}: decider veto -- {decision.get('rationale','')}")
            results.append({"ticker": ticker, "status": "vetoed"})
            continue

        conviction = int(decision.get("conviction") or s.get("conviction") or 0)
        tier_mult = size_tier_multiplier(decision.get("size_tier"))
        target_pct = target_pct_for_conviction(conviction, strategy) * tier_mult
        target_pct = min(target_pct, strategy.get("max_position_pct", 0.05))
        target_dollars = portfolio * target_pct

        pos = positions.get(ticker)
        current_dollars = abs(float(pos.get("market_value", 0))) if pos else 0.0
        delta = target_dollars - current_dollars

        # New name would exceed max open positions?
        if not pos and len(positions) >= max_positions:
            print(f"  [REBAL] {ticker}: skip -- max open positions ({max_positions})")
            results.append({"ticker": ticker, "status": "max_positions"})
            continue

        action = "hold"
        order = None
        side = None
        trade_dollars = 0.0

        if delta >= min_delta:
            # Top up toward target, clamped by remaining total-exposure headroom.
            headroom = max(0.0, max_total - running_exposure)
            add_dollars = min(delta, headroom)
            if add_dollars < min_delta:
                print(f"  [REBAL] {ticker}: at target / no exposure headroom "
                      f"(target ${target_dollars:,.0f}, held ${current_dollars:,.0f})")
                results.append({"ticker": ticker, "status": "at_target"})
                continue
            side = "buy"
            action = "add" if pos else "enter"
            trade_dollars = add_dollars
        elif allow_trim and delta < 0 and abs(delta) >= min_delta:
            side = "sell"
            action = "trim"
            trade_dollars = abs(delta)
        else:
            print(f"  [REBAL] {ticker}: at target "
                  f"(target ${target_dollars:,.0f} ~ held ${current_dollars:,.0f})")
            results.append({"ticker": ticker, "status": "at_target"})
            continue

        # Convert dollars to an order (whole shares unless fractional enabled)
        if allow_fractional:
            qty_desc = f"${trade_dollars:,.0f}"
            notional = round(trade_dollars, 2)
            qty = None
        else:
            qty = int(trade_dollars / price)
            notional = None
            if qty < 1:
                print(f"  [REBAL] {ticker}: {action} ${trade_dollars:,.0f} < 1 share "
                      f"@ ${price:.2f}; enable allow_fractional to deploy. Skipping.")
                results.append({"ticker": ticker, "status": "below_one_share"})
                continue
            qty_desc = f"{qty} sh"

        model_used = decision.get("model_used", "unknown")
        catalyst_date = decision.get("catalyst_date") or s.get("_catalyst_date")
        hold_through = bool(decision.get("hold_through_catalyst", False))
        sl_pct = get_stop_loss_pct(ticker)
        tp_pct = get_take_profit_pct(sl_pct)
        stop_loss_price = round(price * (1 - sl_pct), 2)
        take_profit_price = round(price * (1 + tp_pct), 2)

        print(f"  [REBAL] {action.upper()} {ticker}: {side} {qty_desc} @ ${price:.2f} "
              f"| conv={conviction} target={target_pct*100:.1f}% "
              f"(${target_dollars:,.0f}) held=${current_dollars:,.0f} model={model_used}")

        if dry_run:
            results.append({
                "ticker": ticker, "status": "dry_run", "action": action, "side": side,
                "trade_dollars": round(trade_dollars, 2), "qty": qty, "notional": notional,
                "target_pct": round(target_pct, 4), "conviction": conviction,
            })
            if side == "buy":
                running_exposure += trade_dollars
            continue

        order = submit_order(ticker=ticker, qty=qty or 0, side=side, notional=notional)
        if not order:
            results.append({"ticker": ticker, "status": "order_failed"})
            continue

        order_id = order.get("id", "")
        trade_id = insert_trade(
            ticker=ticker,
            side=side,
            qty=qty or 0,
            price=price,
            order_id=order_id,
            stop_loss=stop_loss_price,
            take_profit=take_profit_price,
            slippage_price_at_signal=price,
            catalyst_date=catalyst_date,
            decision_model=model_used,
            hold_through=hold_through,
        )

        final = wait_for_fill(order_id) or order
        alpaca_status = str(final.get("status", "")).lower()
        filled_qty = float(final.get("filled_qty") or 0)
        filled_avg = float(final.get("filled_avg_price") or 0) or price
        journal_status = "filled" if filled_qty > 0 else alpaca_status
        update_trade_fill(trade_id, status=journal_status, filled_qty=filled_qty,
                          filled_avg_price=filled_avg)

        if filled_qty <= 0:
            print(f"  [REBAL] {ticker}: {alpaca_status}, no fill yet (notional/day orders may settle async)")
            results.append({"ticker": ticker, "status": "submitted_no_fill",
                            "trade_id": trade_id, "order_id": order_id})
            if side == "buy":
                running_exposure += trade_dollars
            continue

        log_slippage_async(trade_id, ticker, filled_avg)
        if side == "buy":
            running_exposure += filled_qty * filled_avg

        if send_discord:
            send_trade_alert(
                ticker=ticker,
                side=side,
                qty=filled_qty,
                price=filled_avg,
                sentiment=_conviction_to_sentiment(side, conviction),
                category=f"Rebalance: {action} (decider: {model_used})",
                confidence=conviction,
                headline=f"Brief {week_of}: scale {ticker} to {target_pct*100:.1f}% target",
                reasoning=decision.get("rationale") or (s.get("thesis") or ""),
            )

        results.append({
            "ticker": ticker, "status": "ordered", "action": action,
            "trade_id": trade_id, "qty": filled_qty, "price": filled_avg,
        })

    acted = [r for r in results if r["status"] in ("ordered", "dry_run")]
    print(f"  [REBAL] Done. {len(acted)} action(s).")
    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scale-to-target rebalancer.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-discord", action="store_true")
    parser.add_argument("--ignore-market-hours", action="store_true")
    args = parser.parse_args()

    rebalance_from_brief(
        dry_run=args.dry_run,
        send_discord=not args.no_discord,
        ignore_market_hours=args.ignore_market_hours,
    )
