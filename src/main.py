"""
Biotech Trading Bot -- Main Scheduler

Orchestrates the full pipeline:
  EDGAR poll -> Keyword filter -> Gemini classify -> Risk check -> Trade -> Discord alert

Uses APScheduler for:
  - Dexter Weekly Brief (Monday 6:00 AM EST)
  - Clinical Tracker (every 6 hours for trial status changes)
"""

import os
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from src.db.schema import init_db
from src.db.migrate_phase2 import migrate as migrate_phase2
from src.db.storage import get_daily_llm_calls
from src.data.edgar_8k import poll_all_tickers
from src.analysis.keyword_filter import filter_headline, should_classify
from src.analysis.gemini_classifier import classify_and_store
from src.trading.executor import get_latest_price, submit_order, get_portfolio_value
from src.trading.risk_manager import (
    calculate_position_size, get_trade_side, should_stop_trading,
)
from src.trading.slippage_log import log_slippage_async
from src.trading.brief_trader import trade_from_brief
from src.trading.rebalancer import rebalance_from_brief
from src.trading.position_manager import manage_positions
from src.config.strategy import load_strategy
from src.alerts.discord import send_trade_alert, send_system_alert, send_daily_summary
from src.db.storage import insert_trade
from src.scout.weekly_poll import run_weekly_scout
from src.scout.clinical_tracker import check_trial_status
from src.scout.weekly_discovery import run_weekly_discovery
from src.scout.dexter_bridge import run_dexter_weekly_brief, run_dexter_daily_pulse


# --- Configuration ---
POLL_INTERVAL_SECONDS = 60   # How often to check for new filings
EDGAR_LOOKBACK_DAYS = 1      # Only look at filings from the last N days


def process_pipeline():
    """Run one cycle of the full pipeline."""
    print(f"\n{'='*60}")
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] Pipeline cycle starting...")
    print(f"{'='*60}")

    # Step 0: Check if we should stop trading
    stop_check = should_stop_trading()
    if stop_check["stop"]:
        print(f"  [RISK] {stop_check['reason']}")
        send_system_alert("Trading Paused", stop_check["reason"])
        return

    # Step 1: Poll EDGAR for new 8-K filings
    new_events = poll_all_tickers(since_days=EDGAR_LOOKBACK_DAYS, fetch_text=True)

    if not new_events:
        print("  [PIPELINE] No new filings found.")
        return

    print(f"  [PIPELINE] {len(new_events)} new filing(s) to process")

    for event in new_events:
        news_id = event["news_id"]
        ticker = event["ticker"]
        headline = event["headline"]

        # Step 2: Keyword filter (Tier 1)
        # Check both the headline AND the raw filing text (if available)
        filter_result = filter_headline(headline)

        # If headline doesn't match, also check raw text from the filing
        if not should_classify(filter_result):
            from src.db.schema import get_connection as _get_conn
            from src.db.schema import is_postgres
            _conn = _get_conn()
            if is_postgres():
                _cur = _conn.cursor()
                _cur.execute(
                    "SELECT raw_text FROM news_events WHERE id = %s", (news_id,)
                )
                _row = _cur.fetchone()
            else:
                _row = _conn.execute(
                    "SELECT raw_text FROM news_events WHERE id = ?", (news_id,)
                ).fetchone()
            _conn.close()
            raw_text = dict(_row).get("raw_text", "") if _row else ""
            if raw_text:
                filter_result = filter_headline(raw_text[:2000])

        print(f"  [FILTER] {ticker}: score={filter_result.score}, "
              f"dir={filter_result.direction}, keywords={[k for k, _ in filter_result.matched_keywords][:5]}")

        if not should_classify(filter_result):
            print(f"  [FILTER] Skipping -- below relevance threshold")
            continue

        # Step 3: Gemini classification (Tier 2)
        extra_context = f"Keyword filter detected: {filter_result.direction} signal with keywords: {filter_result.matched_keywords}"
        classification_id = classify_and_store(
            news_id=news_id,
            headline=headline,
            extra_context=extra_context,
        )

        if not classification_id:
            print(f"  [PIPELINE] Classification skipped or failed for {ticker}")
            continue

        # Read back the classification from DB
        from src.db.schema import get_connection, is_postgres
        conn = get_connection()
        if is_postgres():
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM classified_events WHERE id = %s",
                (classification_id,)
            )
            row = cur.fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM classified_events WHERE id = ?",
                (classification_id,)
            ).fetchone()
        conn.close()

        if not row:
            continue

        classification = dict(row)
        sentiment = classification["sentiment"]
        confidence = classification["confidence"]
        category = classification["category"]
        reasoning = classification.get("reasoning", "")

        # Step 4: Trade decision
        side = get_trade_side(sentiment)
        if side == "skip":
            print(f"  [TRADE] Skipping {ticker} -- neutral sentiment")
            continue

        # Get current price
        price = get_latest_price(ticker)
        if not price:
            print(f"  [TRADE] Cannot get price for {ticker}, skipping")
            continue

        # Calculate position size
        sizing = calculate_position_size(
            ticker=ticker,
            price=price,
            sentiment=sentiment,
            confidence=confidence,
        )

        if not sizing["allowed"]:
            print(f"  [TRADE] {ticker} blocked: {sizing['reason']}")
            continue

        qty = sizing["qty"]
        print(f"  [TRADE] Executing: {side} {qty} {ticker} @ ${price:.2f} "
              f"({sizing['reason']})")

        # Step 5: Submit order
        order = submit_order(ticker=ticker, qty=qty, side=side)

        if order:
            order_id = order.get("id", "")

            # Log trade
            trade_id = insert_trade(
                ticker=ticker,
                side=side,
                qty=qty,
                price=price,
                order_id=order_id,
                news_id=news_id,
                classified_id=classification_id,
                stop_loss=sizing.get("stop_loss_price"),
                take_profit=sizing.get("take_profit_price"),
                slippage_price_at_signal=price,
            )

            # Start slippage logger
            log_slippage_async(trade_id, ticker, price)

            # Send Discord alert
            send_trade_alert(
                ticker=ticker,
                side=side,
                qty=qty,
                price=price,
                sentiment=sentiment,
                category=category,
                confidence=confidence,
                headline=headline,
                reasoning=reasoning,
            )

            print(f"  [TRADE] Order placed: {order_id}")
        else:
            print(f"  [TRADE] Order failed for {ticker}")


def scheduled_weekly_scout():
    """Wrapper for weekly scout to handle exceptions."""
    try:
        print("\n" + "=" * 60)
        print("⏰ SCHEDULED: Weekly Scout (Monday 4:00 AM EST)")
        print("=" * 60)
        run_weekly_scout(send_discord=True)
    except Exception as e:
        print(f"  [ERROR] Weekly scout failed: {e}")
        send_system_alert("Weekly Scout Failed", str(e)[:500])


def scheduled_weekly_discovery():
    """Wrapper for weekly discovery to handle exceptions."""
    try:
        print("\n" + "=" * 60)
        print("⏰ SCHEDULED: Weekly Discovery (Monday 4:15 AM EST)")
        print("=" * 60)
        run_weekly_discovery(send_discord=True)
    except Exception as e:
        print(f"  [ERROR] Weekly discovery failed: {e}")
        send_system_alert("Weekly Discovery Failed", str(e)[:500])


def scheduled_clinical_tracker():
    """Wrapper for clinical tracker to handle exceptions."""
    try:
        print("\n" + "=" * 60)
        print("⏰ SCHEDULED: Clinical Tracker (Every 6 Hours)")
        print("=" * 60)
        check_trial_status()
    except Exception as e:
        print(f"  [ERROR] Clinical tracker failed: {e}")
        send_system_alert("Clinical Tracker Failed", str(e)[:500])


def scheduled_brief_trade():
    """
    Weekday scale-to-target check: size every eligible high-conviction name to
    its conviction-based target and top up toward it (this both enters NEW names
    and increases EXISTING ones as conviction rises). Free Gemini decider gates
    each order; Claude brief is research-only.
    """
    try:
        print("\n" + "=" * 60)
        print("⏰ SCHEDULED: Scale-to-Target Rebalance (Mon-Fri 9:35 AM EST)")
        print("=" * 60)
        rebalance_from_brief(dry_run=False, send_discord=True)
    except Exception as e:
        print(f"  [ERROR] Rebalance failed: {e}")
        send_system_alert("Rebalance Failed", str(e)[:500])


def scheduled_position_manager():
    """Monitor open positions for stop/take-profit/pre-catalyst exits."""
    try:
        strategy = load_strategy()
        manage_positions(dry_run=False, send_discord=True)
    except Exception as e:
        print(f"  [ERROR] Position manager failed: {e}")
        send_system_alert("Position Manager Failed", str(e)[:500])


def scheduled_dexter_weekly_brief():
    """Wrapper for Dexter weekly brief run to handle exceptions."""
    try:
        print("\n" + "=" * 60)
        print("⏰ SCHEDULED: Dexter Weekly Brief (Monday 6:00 AM EST)")
        print("=" * 60)
        result = run_dexter_weekly_brief(timeout_seconds=1200)
        if result.get("ok"):
            print(f"  [DEXTER] Weekly brief completed for tickers: {', '.join(result.get('tickers', []))}")
        else:
            stderr = (result.get("stderr", "") or "")[:500]
            print(f"  [ERROR] Dexter weekly brief failed: {stderr}")
            send_system_alert("Dexter Weekly Brief Failed", stderr or "Unknown error")
    except Exception as e:
        print(f"  [ERROR] Dexter weekly brief crashed: {e}")
        send_system_alert("Dexter Weekly Brief Failed", str(e)[:500])


def scheduled_dexter_daily_pulse():
    """Wrapper for Dexter daily pulse run to handle exceptions."""
    try:
        print("\n" + "=" * 60)
        print("⏰ SCHEDULED: Dexter Daily Pulse (Tue-Fri 6:30 AM EST)")
        print("=" * 60)
        result = run_dexter_daily_pulse(timeout_seconds=600)
        if result.get("ok"):
            print(f"  [DEXTER] Daily pulse completed for tickers: {', '.join(result.get('tickers', []))}")
        else:
            stderr = (result.get("stderr", "") or "")[:500]
            print(f"  [ERROR] Dexter daily pulse failed: {stderr}")
            send_system_alert("Dexter Daily Pulse Failed", stderr or "Unknown error")
    except Exception as e:
        print(f"  [ERROR] Dexter daily pulse crashed: {e}")
        send_system_alert("Dexter Daily Pulse Failed", str(e)[:500])


def run_bot():
    """Main entry point -- runs the bot with APScheduler."""
    print("=" * 60)
    print("BIOTECH TRADING BOT -- Starting")
    print("=" * 60)

    # Initialize database + Phase 2 schema
    init_db()
    try:
        migrate_phase2()
    except Exception as e:
        print(f"  [MIGRATE] Phase 2 migration note: {e}")

    strategy = load_strategy()
    pm_interval = strategy.get("position_manager_interval_min", 5)

    # Send startup notification
    portfolio = get_portfolio_value()
    send_system_alert(
        "Bot Started",
        f"Portfolio: ${portfolio:,.2f}\n"
        f"Dexter Weekly Brief: Monday 6:00 AM EST (Claude research)\n"
        f"Dexter Daily Pulse: Tue-Fri 6:30 AM EST\n"
        f"Scale-to-Target Rebalance: Mon-Fri 9:35 AM EST (free Gemini decider)\n"
        f"Position Manager: every {pm_interval} min (market hours)\n"
        f"Clinical Tracker: Every 6 hours"
    )

    print(f"Portfolio value: ${portfolio:,.2f}")
    print(f"Dexter Weekly Brief: Monday 6:00 AM EST (Claude research)")
    print(f"Dexter Daily Pulse: Tue-Fri 6:30 AM EST")
    print(f"Scale-to-Target Rebalance: Mon-Fri 9:35 AM EST (free Gemini decider)")
    print(f"Position Manager: every {pm_interval} min")
    print(f"Clinical Tracker: Every 6 hours")
    print(f"Press Ctrl+C to stop.\n")

    # Set up APScheduler
    scheduler = BackgroundScheduler(timezone="America/New_York")
    
    # Job 1: Dexter Weekly Brief (Monday 6:00 AM EST)
    scheduler.add_job(
        scheduled_dexter_weekly_brief,
        CronTrigger(
            day_of_week='mon',
            hour=6,
            minute=0,
            timezone='America/New_York'
        ),
        id='dexter_weekly_brief',
        name='Dexter Biotech Weekly Brief'
    )

    # Job 2: Dexter Daily Pulse (Tue-Fri 6:30 AM EST)
    scheduler.add_job(
        scheduled_dexter_daily_pulse,
        CronTrigger(
            day_of_week='tue,wed,thu,fri',
            hour=6,
            minute=30,
            timezone='America/New_York'
        ),
        id='dexter_daily_pulse',
        name='Dexter Biotech Daily Pulse'
    )

    # Job 3: Clinical Tracker (every 6 hours)
    scheduler.add_job(
        scheduled_clinical_tracker,
        IntervalTrigger(hours=6),
        id='clinical_tracker',
        name='Clinical Trial Status Tracker'
    )

    # Job 4: Scale-to-Target Rebalance (Mon-Fri 9:35 AM EST)
    # Enters NEW high-conviction names and tops up EXISTING ones toward their
    # conviction-based target. Idempotent: no-op once a name is at target.
    scheduler.add_job(
        scheduled_brief_trade,
        CronTrigger(
            day_of_week='mon,tue,wed,thu,fri',
            hour=9,
            minute=35,
            timezone='America/New_York'
        ),
        id='brief_trade',
        name='Scale-to-Target Rebalance (free-model gate)'
    )

    # Job 5: Position Manager (every N minutes — exits on stop/TP/pre-catalyst)
    scheduler.add_job(
        scheduled_position_manager,
        IntervalTrigger(minutes=pm_interval),
        id='position_manager',
        name='Position Manager'
    )

    scheduler.start()
    print("  [SCHEDULER] Jobs scheduled:")
    for job in scheduler.get_jobs():
        print(f"    - {job.name}: {job.trigger}")

    try:
        # Keep the main thread alive
        while True:
            time.sleep(60)

    except KeyboardInterrupt:
        print("\n\nShutting down...")
        scheduler.shutdown()
        llm_calls = get_daily_llm_calls()
        send_system_alert("Bot Stopped", f"Gemini calls today: {llm_calls}")


def run_once():
    """Run a single pipeline cycle (useful for testing)."""
    init_db()
    process_pipeline()


def run_scout_now():
    """Run the weekly scout immediately (useful for testing)."""
    init_db()
    run_weekly_scout(send_discord=True)


def run_discovery_now():
    """Run the weekly discovery immediately (useful for testing)."""
    init_db()
    run_weekly_discovery(send_discord=True)


def run_clinical_now():
    """Run the clinical tracker immediately (useful for testing)."""
    init_db()
    check_trial_status()


def run_brief_trade_now(dry_run: bool = False, aggressive: bool = False, top_n: int = None):
    """Execute (or preview) the latest weekly brief on Alpaca immediately."""
    init_db()
    migrate_phase2()
    trade_from_brief(
        high_conviction_only=True,
        top_n=top_n,
        aggressive=aggressive,
        dry_run=dry_run,
        send_discord=True,
    )


def run_position_manager_now(dry_run: bool = False):
    init_db()
    migrate_phase2()
    manage_positions(dry_run=dry_run, send_discord=True)


def run_rebalance_now(dry_run: bool = False):
    """Scale every eligible name to its conviction-based target (enter + top up)."""
    init_db()
    migrate_phase2()
    rebalance_from_brief(dry_run=dry_run, send_discord=True)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Biotech Trading Bot")
    parser.add_argument("--once", action="store_true", help="Run a single cycle and exit")
    parser.add_argument("--scout", action="store_true", help="Run weekly scout immediately and exit")
    parser.add_argument("--discovery", action="store_true", help="Run weekly discovery immediately and exit")
    parser.add_argument("--clinical", action="store_true", help="Run clinical tracker immediately and exit")
    parser.add_argument("--brief-trade", action="store_true",
                        help="Execute the latest weekly brief on Alpaca now and exit")
    parser.add_argument("--brief-dry-run", action="store_true",
                        help="Preview the latest weekly brief trades without ordering")
    parser.add_argument("--aggressive", action="store_true",
                        help="With --brief-trade: full 5%% size on high-conviction names")
    parser.add_argument("--top", type=int, default=None,
                        help="With --brief-trade: only the top N signals by conviction")
    parser.add_argument("--positions", action="store_true",
                        help="Run position manager once and exit")
    parser.add_argument("--positions-dry-run", action="store_true",
                        help="Preview position exits without closing")
    parser.add_argument("--rebalance", action="store_true",
                        help="Scale-to-target rebalance now (enter new + top up existing) and exit")
    parser.add_argument("--rebalance-dry-run", action="store_true",
                        help="Preview scale-to-target rebalance without ordering")
    parser.add_argument("--interval", type=int, default=60, help="Poll interval in seconds")
    parser.add_argument("--lookback", type=int, default=1, help="EDGAR lookback in days")
    args = parser.parse_args()

    POLL_INTERVAL_SECONDS = args.interval
    EDGAR_LOOKBACK_DAYS = args.lookback

    if args.once:
        run_once()
    elif args.scout:
        run_scout_now()
    elif args.discovery:
        run_discovery_now()
    elif args.clinical:
        run_clinical_now()
    elif args.brief_trade or args.brief_dry_run:
        run_brief_trade_now(dry_run=args.brief_dry_run, aggressive=args.aggressive, top_n=args.top)
    elif args.rebalance or args.rebalance_dry_run:
        run_rebalance_now(dry_run=args.rebalance_dry_run)
    elif args.positions or args.positions_dry_run:
        run_position_manager_now(dry_run=args.positions_dry_run)
    else:
        run_bot()
