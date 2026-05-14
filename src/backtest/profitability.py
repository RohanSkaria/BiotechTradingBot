"""
Profitability Calculator

Takes paper trading results and calculates:
- Minimum capital needed for target monthly income
- Expected monthly return based on historical signals
- Break-even analysis (costs vs. returns)
- Recommended position sizing

Run after 4+ weeks of paper trading data.
"""

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from src.db.schema import get_connection


def get_trading_stats(db_path: str = None) -> dict:
    """Pull aggregate trading statistics from the database."""
    conn = get_connection(db_path)

    # Total trades
    total_trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]

    # Trades with P&L data
    trades_with_pnl = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE pnl IS NOT NULL"
    ).fetchone()[0]

    # Win/loss breakdown
    wins = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE pnl > 0"
    ).fetchone()[0]
    losses = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE pnl < 0"
    ).fetchone()[0]

    # Average P&L
    avg_pnl_row = conn.execute(
        "SELECT AVG(pnl) FROM trades WHERE pnl IS NOT NULL"
    ).fetchone()
    avg_pnl = avg_pnl_row[0] if avg_pnl_row[0] is not None else 0

    # Total P&L
    total_pnl_row = conn.execute(
        "SELECT SUM(pnl) FROM trades WHERE pnl IS NOT NULL"
    ).fetchone()
    total_pnl = total_pnl_row[0] if total_pnl_row[0] is not None else 0

    # Average trade size
    avg_size_row = conn.execute(
        "SELECT AVG(qty * price) FROM trades WHERE price IS NOT NULL"
    ).fetchone()
    avg_trade_size = avg_size_row[0] if avg_size_row[0] is not None else 0

    # Date range
    first_trade = conn.execute(
        "SELECT MIN(created_at) FROM trades"
    ).fetchone()[0]
    last_trade = conn.execute(
        "SELECT MAX(created_at) FROM trades"
    ).fetchone()[0]

    # LLM usage
    total_llm_calls = conn.execute(
        "SELECT COALESCE(SUM(call_count), 0) FROM llm_usage"
    ).fetchone()[0]
    total_input_tokens = conn.execute(
        "SELECT COALESCE(SUM(total_input_tokens), 0) FROM llm_usage"
    ).fetchone()[0]
    total_output_tokens = conn.execute(
        "SELECT COALESCE(SUM(total_output_tokens), 0) FROM llm_usage"
    ).fetchone()[0]

    # Slippage stats
    slippage_rows = conn.execute(
        """SELECT slippage_price_at_signal, slippage_price_after_30s
           FROM trades
           WHERE slippage_price_at_signal IS NOT NULL
             AND slippage_price_after_30s IS NOT NULL"""
    ).fetchall()

    avg_slippage_pct = 0
    if slippage_rows:
        slippages = []
        for r in slippage_rows:
            r = dict(r)
            if r["slippage_price_at_signal"] > 0:
                s = abs(r["slippage_price_after_30s"] - r["slippage_price_at_signal"]) / r["slippage_price_at_signal"] * 100
                slippages.append(s)
        if slippages:
            avg_slippage_pct = sum(slippages) / len(slippages)

    # Classification accuracy (signals that led to profitable trades)
    classified_trades = conn.execute(
        """SELECT ce.sentiment, t.pnl
           FROM trades t
           JOIN classified_events ce ON t.classified_id = ce.id
           WHERE t.pnl IS NOT NULL"""
    ).fetchall()

    conn.close()

    return {
        "total_trades": total_trades,
        "trades_with_pnl": trades_with_pnl,
        "wins": wins,
        "losses": losses,
        "win_rate": (wins / trades_with_pnl * 100) if trades_with_pnl > 0 else 0,
        "avg_pnl": round(avg_pnl, 2),
        "total_pnl": round(total_pnl, 2),
        "avg_trade_size": round(avg_trade_size, 2),
        "first_trade": first_trade,
        "last_trade": last_trade,
        "total_llm_calls": total_llm_calls,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "avg_slippage_pct": round(avg_slippage_pct, 3),
    }


def calculate_profitability(
    stats: dict,
    monthly_api_cost: float = 0.0,
    target_monthly_income: float = 500.0,
) -> dict:
    """
    Calculate profitability metrics and minimum capital requirements.

    Args:
        stats: Output from get_trading_stats()
        monthly_api_cost: Estimated monthly LLM API cost (likely $0 with Gemini free tier)
        target_monthly_income: Desired monthly income from trading

    Returns:
        Dict with profitability analysis.
    """
    # Estimate trades per month
    if stats["first_trade"] and stats["last_trade"]:
        try:
            first = datetime.fromisoformat(stats["first_trade"])
            last = datetime.fromisoformat(stats["last_trade"])
            days_active = max((last - first).days, 1)
            trades_per_day = stats["total_trades"] / days_active
            trades_per_month = trades_per_day * 22  # ~22 trading days/month
        except (ValueError, TypeError):
            trades_per_month = stats["total_trades"]  # fallback
    else:
        trades_per_month = 0

    # Average return per trade (as percentage of trade size)
    if stats["avg_trade_size"] > 0 and stats["trades_with_pnl"] > 0:
        avg_return_pct = stats["avg_pnl"] / stats["avg_trade_size"] * 100
    else:
        avg_return_pct = 0

    # Expected monthly return percentage
    expected_monthly_return_pct = avg_return_pct * trades_per_month if trades_per_month > 0 else 0

    # Minimum capital calculations
    if expected_monthly_return_pct > 0:
        # Capital needed to cover costs
        min_capital_breakeven = (monthly_api_cost / (expected_monthly_return_pct / 100)) if expected_monthly_return_pct > 0 else float('inf')

        # Capital needed for target income
        min_capital_target = (
            (target_monthly_income + monthly_api_cost) / (expected_monthly_return_pct / 100)
        )
    else:
        min_capital_breakeven = float('inf')
        min_capital_target = float('inf')

    # Sharpe-like ratio (simplified)
    # Using avg_pnl as return and assuming we'd need std dev from actual trades
    risk_reward = abs(stats["avg_pnl"]) / max(stats["avg_slippage_pct"], 0.01) if stats["avg_pnl"] != 0 else 0

    return {
        "trades_per_month": round(trades_per_month, 1),
        "avg_return_per_trade_pct": round(avg_return_pct, 2),
        "expected_monthly_return_pct": round(expected_monthly_return_pct, 2),
        "monthly_api_cost": monthly_api_cost,
        "min_capital_breakeven": round(min_capital_breakeven, 2),
        "min_capital_for_target": round(min_capital_target, 2),
        "target_monthly_income": target_monthly_income,
        "win_rate": stats["win_rate"],
        "avg_slippage_pct": stats["avg_slippage_pct"],
        "risk_reward_ratio": round(risk_reward, 2),
    }


def print_profitability_report(stats: dict, profitability: dict) -> None:
    """Pretty-print the profitability analysis."""
    print("\n" + "=" * 60)
    print("PROFITABILITY ANALYSIS")
    print("=" * 60)

    print(f"\n--- Trading Performance ---")
    print(f"  Total Trades:          {stats['total_trades']}")
    print(f"  Wins / Losses:         {stats['wins']} / {stats['losses']}")
    print(f"  Win Rate:              {stats['win_rate']:.1f}%")
    print(f"  Avg P&L per Trade:     ${stats['avg_pnl']:.2f}")
    print(f"  Total P&L:             ${stats['total_pnl']:.2f}")
    print(f"  Avg Trade Size:        ${stats['avg_trade_size']:.2f}")
    print(f"  Avg Slippage:          {stats['avg_slippage_pct']:.3f}%")

    print(f"\n--- LLM Usage ---")
    print(f"  Total Gemini Calls:    {stats['total_llm_calls']}")
    print(f"  Total Tokens:          {stats['total_input_tokens'] + stats['total_output_tokens']:,}")
    print(f"  Monthly API Cost:      ${profitability['monthly_api_cost']:.2f}")

    print(f"\n--- Projections ---")
    print(f"  Est. Trades/Month:     {profitability['trades_per_month']}")
    print(f"  Avg Return/Trade:      {profitability['avg_return_per_trade_pct']:.2f}%")
    print(f"  Expected Monthly Return: {profitability['expected_monthly_return_pct']:.2f}%")

    print(f"\n--- Capital Requirements ---")
    if profitability['min_capital_breakeven'] < float('inf'):
        print(f"  To Break Even:         ${profitability['min_capital_breakeven']:,.2f}")
        print(f"  For ${profitability['target_monthly_income']:.0f}/mo income:  ${profitability['min_capital_for_target']:,.2f}")
    else:
        print(f"  Insufficient data to calculate capital requirements.")
        print(f"  Need more trades with realized P&L to project returns.")

    # Scenario table
    print(f"\n--- Portfolio Size Scenarios (at {profitability['expected_monthly_return_pct']:.2f}% monthly return) ---")
    if profitability['expected_monthly_return_pct'] > 0:
        for capital in [1000, 5000, 10000, 25000, 50000, 100000]:
            monthly = capital * profitability['expected_monthly_return_pct'] / 100
            annual = monthly * 12
            net = monthly - profitability['monthly_api_cost']
            print(f"  ${capital:>8,} -> ${monthly:>8,.2f}/mo  ${annual:>10,.2f}/yr  (net: ${net:>8,.2f}/mo)")
    else:
        print(f"  No positive return data available yet.")

    print(f"\n--- Recommendation ---")
    if stats['total_trades'] < 10:
        print(f"  Need more trades (currently {stats['total_trades']}). Run paper trading for 4+ weeks.")
    elif stats['win_rate'] < 50:
        print(f"  Win rate ({stats['win_rate']:.1f}%) is below 50%. Tune classification before deploying capital.")
    elif profitability['expected_monthly_return_pct'] <= 0:
        print(f"  Expected return is negative. Review strategy before deploying capital.")
    else:
        rec_capital = profitability['min_capital_for_target']
        print(f"  Strategy shows promise. Recommended starting capital: ${rec_capital:,.2f}")
        print(f"  This targets ${profitability['target_monthly_income']:.0f}/month at current performance.")

    print()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Profitability Calculator")
    parser.add_argument("--target", type=float, default=500, help="Target monthly income ($)")
    parser.add_argument("--api-cost", type=float, default=0, help="Monthly API cost ($)")
    args = parser.parse_args()

    stats = get_trading_stats()
    profitability = calculate_profitability(
        stats,
        monthly_api_cost=args.api_cost,
        target_monthly_income=args.target,
    )
    print_profitability_report(stats, profitability)
