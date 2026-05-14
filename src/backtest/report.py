"""
Backtesting Performance Report

Generates accuracy, precision/recall by category, and average return metrics
from evaluated signals.
"""

from collections import defaultdict
from typing import Optional


def generate_report(evaluations: list) -> Optional[dict]:
    """
    Generate a performance report from a list of evaluated signals.

    Args:
        evaluations: List of dicts from evaluator.evaluate_signal()

    Returns:
        Dict with performance metrics.
    """
    if not evaluations:
        print("No evaluations to report on.")
        return None

    total = len(evaluations)
    correct = sum(1 for e in evaluations if e["correct"])
    accuracy = correct / total * 100

    # Average simulated P&L
    avg_pnl = sum(e["simulated_pnl_pct"] for e in evaluations) / total

    # Breakdown by sentiment
    by_sentiment = defaultdict(list)
    for e in evaluations:
        by_sentiment[e["predicted_sentiment"]].append(e)

    sentiment_stats = {}
    for sentiment, evals in by_sentiment.items():
        n = len(evals)
        c = sum(1 for e in evals if e["correct"])
        avg = sum(e["simulated_pnl_pct"] for e in evals) / n
        sentiment_stats[sentiment] = {
            "count": n,
            "correct": c,
            "accuracy": round(c / n * 100, 1),
            "avg_pnl_pct": round(avg, 2),
        }

    # Win rate (excluding neutral)
    trades = [e for e in evaluations if e["expected_direction"] != 0]
    if trades:
        wins = sum(1 for e in trades if e["simulated_pnl_pct"] > 0)
        win_rate = wins / len(trades) * 100
    else:
        win_rate = 0

    report = {
        "total_signals": total,
        "correct_predictions": correct,
        "accuracy_pct": round(accuracy, 1),
        "avg_simulated_pnl_pct": round(avg_pnl, 2),
        "win_rate_pct": round(win_rate, 1),
        "total_trades": len(trades),
        "by_sentiment": sentiment_stats,
    }

    return report


def print_report(report: dict) -> None:
    """Pretty-print a backtest report."""
    if not report:
        return

    print("\n" + "=" * 60)
    print("BACKTEST PERFORMANCE REPORT")
    print("=" * 60)
    print(f"  Total Signals:       {report['total_signals']}")
    print(f"  Correct Predictions: {report['correct_predictions']}")
    print(f"  Accuracy:            {report['accuracy_pct']}%")
    print(f"  Win Rate (trades):   {report['win_rate_pct']}%")
    print(f"  Avg Simulated P&L:   {report['avg_simulated_pnl_pct']}%")
    print(f"  Total Trades:        {report['total_trades']}")

    print(f"\n  {'Sentiment':<20s} {'Count':>6s} {'Accuracy':>10s} {'Avg P&L':>10s}")
    print(f"  {'-'*20} {'-'*6} {'-'*10} {'-'*10}")
    for sentiment, stats in report["by_sentiment"].items():
        print(f"  {sentiment:<20s} {stats['count']:>6d} {stats['accuracy']:>9.1f}% {stats['avg_pnl_pct']:>9.2f}%")

    print()


if __name__ == "__main__":
    # Example with synthetic data
    sample_evals = [
        {"ticker": "LLY", "event_date": "2026-02-04", "predicted_sentiment": "Strong Positive",
         "expected_direction": 1, "actual_return_pct": 3.5, "correct": True,
         "magnitude": 3.5, "simulated_pnl_pct": 3.5, "price_at_event": 800, "volume": 5000000},
        {"ticker": "NVO", "event_date": "2026-01-15", "predicted_sentiment": "Strong Negative",
         "expected_direction": -1, "actual_return_pct": -7.2, "correct": True,
         "magnitude": 7.2, "simulated_pnl_pct": 7.2, "price_at_event": 120, "volume": 3000000},
        {"ticker": "CRSP", "event_date": "2026-01-12", "predicted_sentiment": "Weak Positive",
         "expected_direction": 1, "actual_return_pct": -1.0, "correct": False,
         "magnitude": 1.0, "simulated_pnl_pct": -1.0, "price_at_event": 55, "volume": 1000000},
    ]
    report = generate_report(sample_evals)
    print_report(report)
