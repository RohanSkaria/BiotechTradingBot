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
    # Scale-to-target rebalancing
    "rebalance_enabled": True,
    "rebalance_min_conviction": 70,
    "target_floor_pct": 0.02,
    "rebalance_min_delta_pct": 0.01,
    "rebalance_allow_trim": False,
    # Order execution
    "allow_fractional": False,
    "min_order_notional": 1.0,
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
