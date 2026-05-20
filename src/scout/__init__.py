"""
Scout module for catalyst detection, clinical trial tracking, and ticker discovery.

Components:
- weekly_poll: Monday 4AM catalyst scan for watchlist tickers
- clinical_tracker: Every 6 hours - monitors ClinicalTrials.gov for status changes
- weekly_discovery: Monday 4AM - AI-powered discovery of new biotech catalysts
"""

from .weekly_poll import run_weekly_scout
from .clinical_tracker import check_trial_status, discover_and_add_trials
from .weekly_discovery import run_weekly_discovery
from .dexter_bridge import (
    run_dexter_weekly_brief,
    run_dexter_daily_pulse,
    get_weekly_brief_tickers,
)

__all__ = [
    'run_weekly_scout',
    'check_trial_status',
    'discover_and_add_trials', 
    'run_weekly_discovery',
    'run_dexter_weekly_brief',
    'run_dexter_daily_pulse',
    'get_weekly_brief_tickers',
]
