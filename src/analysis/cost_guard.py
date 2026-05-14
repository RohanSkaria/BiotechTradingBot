"""
LLM Cost Guard

Prevents runaway Gemini API spend by enforcing:
- Daily call counter (hard cap)
- Headline dedup (skip already-classified headlines)
- Free tier awareness (model fallback chain)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from src.db.storage import get_daily_llm_calls, is_headline_seen

# Hard cap: max Gemini calls per day
DAILY_CALL_LIMIT = 100

# Models to try in order (cheapest / free-tier first)
MODEL_FALLBACK_CHAIN = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
]

# Default model to use
DEFAULT_MODEL = MODEL_FALLBACK_CHAIN[0]


def can_classify(headline: str = None, db_path: str = None) -> dict:
    """
    Check if we're allowed to make an LLM call.
    Returns dict with 'allowed' bool and 'reason' string.
    """
    # Check daily limit
    daily_calls = get_daily_llm_calls(db_path=db_path)
    if daily_calls >= DAILY_CALL_LIMIT:
        return {
            "allowed": False,
            "reason": f"Daily limit reached ({daily_calls}/{DAILY_CALL_LIMIT})",
            "daily_calls": daily_calls,
        }

    # Check headline dedup
    if headline and is_headline_seen(headline, db_path=db_path):
        return {
            "allowed": False,
            "reason": "Headline already classified (dedup)",
            "daily_calls": daily_calls,
        }

    return {
        "allowed": True,
        "reason": "OK",
        "daily_calls": daily_calls,
        "remaining": DAILY_CALL_LIMIT - daily_calls,
    }


def get_model_chain() -> list:
    """Return the model fallback chain."""
    return MODEL_FALLBACK_CHAIN.copy()
