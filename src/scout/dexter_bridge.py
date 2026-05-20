"""
Dexter bridge for running the weekly biotech brief from Python scheduler.
"""

import os
import subprocess
from typing import Dict, List

from src.db.storage import get_watchlist_from_db


DEFAULT_TICKERS = ["LLY", "VRTX", "CRSP", "REGN", "AMGN", "GILD"]


def _project_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _dexter_dir() -> str:
    return os.path.join(_project_root(), "dexter")


def get_weekly_brief_tickers() -> List[str]:
    """
    Build the weekly brief ticker universe.

    Always includes the core biotech anchors (DEFAULT_TICKERS); unions any active
    rows from weekly_watchlist on top. The core list is immutable from Dexter's
    side so discovery cannot accidentally drop it.
    """
    try:
        rows = get_watchlist_from_db()
        discovered = {
            str(r.get("ticker", "")).strip().upper()
            for r in rows
            if r.get("ticker")
        }
        discovered.discard("")
        return sorted(set(DEFAULT_TICKERS) | discovered)
    except Exception:
        return list(DEFAULT_TICKERS)


def run_dexter_weekly_brief(timeout_seconds: int = 1200) -> Dict:
    """
    Invoke Dexter's weekly-brief script using Bun.

    Returns:
      {
        "ok": bool,
        "tickers": list[str],
        "stdout": str,
        "stderr": str,
        "code": int,
      }
    """
    dexter_path = _dexter_dir()
    if not os.path.isdir(dexter_path):
        return {
            "ok": False,
            "tickers": [],
            "stdout": "",
            "stderr": f"Dexter directory not found: {dexter_path}",
            "code": -1,
        }

    tickers = get_weekly_brief_tickers()
    cmd = ["bun", "run", "scripts/weekly-brief.ts", *tickers]
    env = os.environ.copy()
    env.setdefault("WEEKLY_BRIEF_MODEL", "claude-sonnet-4-5")

    try:
        result = subprocess.run(
            cmd,
            cwd=dexter_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        return {
            "ok": result.returncode == 0,
            "tickers": tickers,
            "stdout": result.stdout or "",
            "stderr": result.stderr or "",
            "code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "tickers": tickers,
            "stdout": "",
            "stderr": f"Timed out after {timeout_seconds}s",
            "code": -2,
        }


def run_dexter_daily_pulse(timeout_seconds: int = 600) -> Dict:
    """
    Invoke Dexter's daily-pulse script using Bun.

    Returns:
      {
        "ok": bool,
        "tickers": list[str],
        "stdout": str,
        "stderr": str,
        "code": int,
      }
    """
    dexter_path = _dexter_dir()
    if not os.path.isdir(dexter_path):
        return {
            "ok": False,
            "tickers": [],
            "stdout": "",
            "stderr": f"Dexter directory not found: {dexter_path}",
            "code": -1,
        }

    tickers = get_weekly_brief_tickers()
    cmd = ["bun", "run", "scripts/daily-pulse.ts"]
    env = os.environ.copy()
    env.setdefault("DAILY_PULSE_MODEL", "gemini-2.5-flash")
    if (not env.get("GOOGLE_API_KEY")) and env.get("GEMINI_API_KEY"):
        env["GOOGLE_API_KEY"] = env["GEMINI_API_KEY"]

    try:
        result = subprocess.run(
            cmd,
            cwd=dexter_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        return {
            "ok": result.returncode == 0,
            "tickers": tickers,
            "stdout": result.stdout or "",
            "stderr": result.stderr or "",
            "code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "tickers": tickers,
            "stdout": "",
            "stderr": f"Timed out after {timeout_seconds}s",
            "code": -2,
        }
