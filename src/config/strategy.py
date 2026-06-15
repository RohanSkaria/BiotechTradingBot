"""
Load strategy guardrails from config/strategy.yaml.
"""

import os
import yaml
from functools import lru_cache

STRATEGY_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "config", "strategy.yaml"
)

DEFAULTS = {
    "max_position_pct": 0.05,
    "max_total_exposure_pct": 0.25,
    "max_open_positions": 5,
    "max_daily_loss_pct": 0.02,
    "entry_window_days": 10,
    "pre_catalyst_exit_days": 1,
    "default_hold_through_catalyst": False,
    "trade_decider_model": "gemini-2.0-flash",
    "position_manager_interval_min": 5,
    "size_tier_multipliers": {
        "full": 1.0,
        "half": 0.5,
        "quarter": 0.25,
        "none": 0.0,
    },
}


@lru_cache(maxsize=1)
def load_strategy() -> dict:
    """Load strategy config, falling back to defaults for missing keys."""
    cfg = dict(DEFAULTS)
    try:
        with open(STRATEGY_PATH, "r") as f:
            loaded = yaml.safe_load(f) or {}
        cfg.update(loaded)
    except Exception:
        pass
    return cfg


def get(key: str, default=None):
    return load_strategy().get(key, default)
