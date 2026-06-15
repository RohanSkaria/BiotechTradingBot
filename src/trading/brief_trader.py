"""
Weekly Brief Trader -- bridges Dexter analysis to Alpaca execution.

Flow:
  1. Read latest Claude weekly brief (PAID research only)
  2. Filter to high-conviction signals within entry_window_days of catalyst
  3. Free Gemini trade decider ($0) gates each candidate
  4. Risk manager sizes + Alpaca executes
  5. Position manager handles exits (stop / take-profit / pre-catalyst)

Usage:
    python -m src.trading.brief_trader --dry-run
    python -m src.trading.brief_trader
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.config.strategy import load_strategy
from src.db.storage import (
    get_latest_brief_signals,
    get_watchlist_row,
    insert_trade,
    update_trade_fill,
)
from src.analysis.trade_decider import (
    decide_trade,
    extract_catalyst_date,
    days_until,
    size_tier_multiplier,
)
from src.trading.executor import (
    get_latest_price,
    submit_order,
    get_portfolio_value,
    get_clock,
    wait_for_fill,
)
from src.trading.risk_manager import calculate_position_size, should_stop_trading
from src.trading.slippage_log import log_slippage_async
from src.alerts.discord import send_trade_alert, send_system_alert


DIRECTION_TO_SIDE = {"long": "buy", "short": "sell"}


def _conviction_to_sentiment(side: str, conviction: int) -> str:
    strong = conviction >= 70
    if side == "buy":
        return "Strong Positive" if strong else "Weak Positive"
    return "Strong Negative" if strong else "Weak Negative"


def select_signals(
    signals: list,
    high_conviction_only: bool = True,
    min_conviction: int = 0,
    top_n: int = None,
    entry_window_days: int = None,
) -> list:
    """Filter + rank brief signals into candidates for the free-model decider."""
    strategy = load_strategy()
    window = entry_window_days if entry_window_days is not None else strategy.get("entry_window_days", 10)
    picks = []

    for s in signals:
        direction = str(s.get("direction") or "").lower()
        if direction not in DIRECTION_TO_SIDE:
            continue
        if high_conviction_only and not s.get("high_conviction"):
            continue
        if int(s.get("conviction") or 0) < min_conviction:
            continue

        watchlist_row = get_watchlist_row(str(s.get("ticker", "")).upper())
        catalyst_date = extract_catalyst_date(s, watchlist_row)
        days = days_until(catalyst_date) if catalyst_date else None

        # Require a dated catalyst within the entry window (0 = catalyst day still ok for pre-catalyst exit logic)
        if days is None:
            continue
        if days < 0 or days > window:
            continue

        s = dict(s)
        s["_catalyst_date"] = catalyst_date
        s["_days_to_catalyst"] = days
        picks.append(s)

    picks.sort(key=lambda x: int(x.get("conviction") or 0), reverse=True)
    if top_n:
        picks = picks[:top_n]
    return picks


def trade_from_brief(
    high_conviction_only: bool = True,
    min_conviction: int = 0,
    top_n: int = None,
    aggressive: bool = False,
    dry_run: bool = False,
    send_discord: bool = True,
    ignore_market_hours: bool = False,
    skip_decider: bool = False,
) -> list:
    """
    Read the latest weekly brief, run free-model gate, place paper trades.
    """
    strategy = load_strategy()
    print("=" * 60)
    print("WEEKLY BRIEF TRADER (free-model gate)")
    print("=" * 60)

    clock = get_clock()
    is_open = clock.get("is_open") if clock else None
    if clock is not None:
        print(f"  [CLOCK] Market open: {is_open}")
    if is_open is False and not dry_run and not ignore_market_hours:
        msg = "Market is closed -- skipping brief trades."
        print(f"  [TRADER] {msg}")
        if send_discord:
            send_system_alert("Brief Trader Skipped", msg)
        return []

    stop = should_stop_trading()
    if stop["stop"]:
        print(f"  [RISK] {stop['reason']}")
        if send_discord:
            send_system_alert("Brief Trader Paused", stop["reason"])
        return []

    signals = get_latest_brief_signals()
    if not signals:
        print("  [TRADER] No weekly brief signals found.")
        return []

    week_of = signals[0].get("week_of")
    picks = select_signals(signals, high_conviction_only, min_conviction, top_n)
    print(
        f"  [TRADER] Week of {week_of}: {len(signals)} signal(s), "
        f"{len(picks)} within {strategy.get('entry_window_days')}d catalyst window"
    )
    if dry_run:
        print("  [TRADER] DRY RUN -- no orders will be submitted")

    if not picks:
        print("  [TRADER] Nothing actionable in entry window.")
        return []

    portfolio = get_portfolio_value()
    print(f"  [TRADER] Portfolio: ${portfolio:,.2f}")

    results = []
    for s in picks:
        ticker = str(s["ticker"]).upper()
        thesis = s.get("thesis") or ""
        research_conviction = int(s.get("conviction") or 0)
        watchlist_row = get_watchlist_row(ticker)

        price = get_latest_price(ticker)
        if not price:
            results.append({"ticker": ticker, "status": "no_price"})
            continue

        # --- Free Gemini trade decider ($0) ---
        if skip_decider:
            decision = {
                "act": True,
                "direction": str(s.get("direction")).lower(),
                "conviction": research_conviction,
                "size_tier": "full" if aggressive else "half",
                "hold_through_catalyst": strategy.get("default_hold_through_catalyst", False),
                "catalyst_date": s.get("_catalyst_date"),
                "model_used": "skip_decider",
                "rationale": "Decider skipped (dry-run fallback)",
            }
        else:
            decision = decide_trade(s, price, watchlist_row)
            if not decision:
                results.append({"ticker": ticker, "status": "decider_failed"})
                continue

        if not decision.get("act"):
            print(f"  [TRADER] {ticker}: decider veto — {decision.get('rationale', '')}")
            results.append({"ticker": ticker, "status": "vetoed", "rationale": decision.get("rationale")})
            continue

        tier = str(decision.get("size_tier") or "none").lower()
        tier_mult = size_tier_multiplier(tier)
        if tier_mult <= 0:
            results.append({"ticker": ticker, "status": "size_tier_none"})
            continue

        direction = str(decision.get("direction") or s.get("direction")).lower()
        side = DIRECTION_TO_SIDE.get(direction)
        if not side:
            continue

        conviction = int(decision.get("conviction") or research_conviction)
        if aggressive:
            conviction = max(conviction, 80)

        sentiment = _conviction_to_sentiment(side, conviction)
        sizing = calculate_position_size(
            ticker=ticker,
            price=price,
            sentiment=sentiment,
            confidence=conviction,
            size_tier_multiplier=tier_mult,
            side=side,
        )
        if not sizing["allowed"]:
            print(f"  [TRADER] {ticker}: blocked — {sizing['reason']}")
            results.append({"ticker": ticker, "status": "blocked", "reason": sizing["reason"]})
            continue

        qty = sizing["qty"]
        catalyst_date = decision.get("catalyst_date") or s.get("_catalyst_date")
        hold_through = bool(decision.get("hold_through_catalyst", False))
        model_used = decision.get("model_used", "unknown")

        print(
            f"  [TRADER] {side.upper()} {qty} {ticker} @ ${price:.2f} "
            f"| tier={tier} conv={conviction} model={model_used} "
            f"| catalyst={catalyst_date} hold_through={hold_through}"
        )

        if dry_run:
            results.append({
                "ticker": ticker, "status": "dry_run", "side": side, "qty": qty,
                "price": price, "decision_model": model_used,
                "catalyst_date": catalyst_date, "hold_through": hold_through,
            })
            continue

        order = submit_order(ticker=ticker, qty=qty, side=side)
        if not order:
            results.append({"ticker": ticker, "status": "order_failed"})
            continue

        order_id = order.get("id", "")
        trade_id = insert_trade(
            ticker=ticker,
            side=side,
            qty=qty,
            price=price,
            order_id=order_id,
            stop_loss=sizing.get("stop_loss_price"),
            take_profit=sizing.get("take_profit_price"),
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
        update_trade_fill(
            trade_id,
            status=journal_status,
            filled_qty=filled_qty,
            filled_avg_price=filled_avg,
        )

        if filled_qty <= 0:
            results.append({"ticker": ticker, "status": "no_fill", "trade_id": trade_id})
            continue

        log_slippage_async(trade_id, ticker, filled_avg)

        if send_discord:
            send_trade_alert(
                ticker=ticker,
                side=side,
                qty=int(filled_qty),
                price=filled_avg,
                sentiment=sentiment,
                category=f"Weekly Brief (decider: {model_used})",
                confidence=conviction,
                headline=f"Brief {week_of}: {direction.upper()} {ticker}",
                reasoning=decision.get("rationale") or thesis,
            )

        results.append({
            "ticker": ticker, "status": "ordered", "trade_id": trade_id,
            "qty": filled_qty, "price": filled_avg, "decision_model": model_used,
        })

    ordered = [r for r in results if r["status"] == "ordered"]
    print(f"  [TRADER] Done. {len(ordered)} order(s) submitted.")
    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Trade weekly brief via free-model gate.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--top", type=int, default=None)
    parser.add_argument("--min-conviction", type=int, default=0)
    parser.add_argument("--all-signals", action="store_true")
    parser.add_argument("--aggressive", action="store_true")
    parser.add_argument("--no-discord", action="store_true")
    parser.add_argument("--ignore-market-hours", action="store_true")
    parser.add_argument("--skip-decider", action="store_true", help="Bypass Gemini gate (testing only)")
    args = parser.parse_args()

    trade_from_brief(
        high_conviction_only=not args.all_signals,
        min_conviction=args.min_conviction,
        top_n=args.top,
        aggressive=args.aggressive,
        dry_run=args.dry_run,
        send_discord=not args.no_discord,
        ignore_market_hours=args.ignore_market_hours,
        skip_decider=args.skip_decider,
    )
