"""
Signal Evaluator

Compares classified sentiment against actual price movements
to measure the accuracy of the classification pipeline.
"""

import os
import sys
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from src.backtest.price_data import get_price_around_event


# Map sentiment to expected direction
SENTIMENT_DIRECTION = {
    "Strong Positive": 1,
    "Weak Positive": 1,
    "Neutral": 0,
    "Weak Negative": -1,
    "Strong Negative": -1,
}


def evaluate_signal(
    ticker: str,
    event_date: str,
    predicted_sentiment: str,
    window: str = "1d",
) -> Optional[dict]:
    """
    Evaluate a single signal by comparing predicted sentiment to actual price movement.

    Args:
        ticker: Stock ticker
        event_date: "YYYY-MM-DD"
        predicted_sentiment: One of the sentiment values
        window: Return window to check ("1d", "2d", "3d")

    Returns:
        Dict with evaluation results, or None if price data unavailable.
    """
    price_data = get_price_around_event(ticker, event_date)
    if not price_data or window not in price_data.get("returns", {}):
        return None

    actual_return = price_data["returns"][window]
    expected_direction = SENTIMENT_DIRECTION.get(predicted_sentiment, 0)

    # Determine if prediction was correct
    if expected_direction == 0:
        # Neutral prediction -- correct if move is small (< 1%)
        correct = abs(actual_return) < 1.0
    elif expected_direction > 0:
        correct = actual_return > 0
    else:
        correct = actual_return < 0

    # Magnitude of the move
    magnitude = abs(actual_return)

    # Profit/loss if we had traded on this signal
    # Assume: buy on positive, short on negative, skip on neutral
    if expected_direction > 0:
        simulated_pnl = actual_return
    elif expected_direction < 0:
        simulated_pnl = -actual_return  # profit from short
    else:
        simulated_pnl = 0.0

    return {
        "ticker": ticker,
        "event_date": event_date,
        "predicted_sentiment": predicted_sentiment,
        "expected_direction": expected_direction,
        "actual_return_pct": actual_return,
        "correct": correct,
        "magnitude": magnitude,
        "simulated_pnl_pct": round(simulated_pnl, 2),
        "price_at_event": price_data["price_at_event"],
        "volume": price_data["volume_at_event"],
    }


def evaluate_batch(signals: list, window: str = "1d") -> list:
    """
    Evaluate a batch of signals.

    Args:
        signals: List of dicts with keys: ticker, event_date, predicted_sentiment
        window: Return window to check

    Returns:
        List of evaluation result dicts.
    """
    results = []
    for signal in signals:
        result = evaluate_signal(
            ticker=signal["ticker"],
            event_date=signal["event_date"],
            predicted_sentiment=signal["predicted_sentiment"],
            window=window,
        )
        if result:
            results.append(result)
        else:
            print(f"  [EVAL] No price data for {signal['ticker']} on {signal['event_date']}")
    return results
