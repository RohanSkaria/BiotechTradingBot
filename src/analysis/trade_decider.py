"""
Free-model trade decision gate.

Claude (paid) produces the weekly research brief. This module uses the free
Gemini chain (cost_guard) as a second-opinion gate before any order is sent to
Alpaca. It can veto over-optimistic research calls and set size tier +
pre-catalyst exit policy per trade.
"""

import os
import json
import re
import sys
from datetime import date, datetime
from typing import Optional

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.analysis.cost_guard import can_classify, get_model_chain
from src.config.strategy import load_strategy
from src.db.storage import increment_llm_usage
from src.trading.risk_manager import get_ticker_info

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

TRADE_DECISION_PROMPT = """You are a biotech catalyst trade decider for a paper trading bot.
You receive RESEARCH from a paid analyst model (weekly brief) plus live market context.
Your job is to decide whether to ACT on the research for algorithmic execution.

Research signal:
- ticker: {ticker}
- research_direction: {research_direction}
- research_conviction: {research_conviction}
- thesis: {thesis}
- catalysts: {catalysts}
- high_conviction_flag: {high_conviction}

Live context:
- current_price: ${current_price}
- change_5d_pct: {change_5d_pct}%
- days_to_catalyst: {days_to_catalyst}
- market_cap_tier: {market_cap_tier}
- default_hold_through_catalyst: {default_hold_through}

Rules:
1. Biotech PDUFA/FDA binaries gap through stops — default is NOT to hold through catalyst.
2. Veto (act=false) if catalyst timing is unclear, thesis is weak, or price already ran >15% in 5 days without new info.
3. Prefer entering when catalyst is 3-10 days out (pre-run-up window).
4. size_tier: full / half / quarter / none — scale by conviction AND clarity of catalyst date.
5. direction must be "long" or "short" when act=true.

Respond ONLY with valid JSON (no markdown fences):
{{
  "act": <true or false>,
  "direction": "<long or short>",
  "conviction": <0-100 integer>,
  "size_tier": "<full|half|quarter|none>",
  "hold_through_catalyst": <true or false>,
  "catalyst_date": "<YYYY-MM-DD or null>",
  "rationale": "<one sentence>"
}}"""


def _parse_catalyst_date_from_text(text: str) -> Optional[str]:
    """Extract YYYY-MM-DD from catalyst strings."""
    if not text:
        return None
    m = re.search(r"(20\d{2}-\d{2}-\d{2})", str(text))
    return m.group(1) if m else None


def extract_catalyst_date(signal: dict, watchlist_row: dict = None) -> Optional[str]:
    """
    Derive catalyst date from brief catalysts jsonb and/or watchlist expected_catalyst.
    """
    catalysts = signal.get("catalysts") or []
    if isinstance(catalysts, str):
        try:
            catalysts = json.loads(catalysts)
        except json.JSONDecodeError:
            catalysts = []

    for c in catalysts:
        if isinstance(c, dict):
            d = c.get("date")
            if d and re.match(r"20\d{2}-\d{2}-\d{2}", str(d)):
                return str(d)[:10]
            event = c.get("event") or ""
            parsed = _parse_catalyst_date_from_text(event)
            if parsed:
                return parsed

    if watchlist_row:
        parsed = _parse_catalyst_date_from_text(watchlist_row.get("expected_catalyst") or "")
        if parsed:
            return parsed

    return None


def days_until(catalyst_date: str, today: date = None) -> Optional[int]:
    if not catalyst_date:
        return None
    today = today or date.today()
    try:
        target = datetime.strptime(catalyst_date[:10], "%Y-%m-%d").date()
        return (target - today).days
    except ValueError:
        return None


def get_change_5d_pct(ticker: str) -> Optional[float]:
    """5-day price change via yfinance."""
    try:
        import yfinance as yf

        hist = yf.Ticker(ticker).history(period="8d")
        if hist is None or len(hist) < 2:
            return None
        start = float(hist.iloc[0]["Close"])
        end = float(hist.iloc[-1]["Close"])
        if start <= 0:
            return None
        return round((end - start) / start * 100, 2)
    except Exception:
        return None


def decide_trade(
    signal: dict,
    current_price: float,
    watchlist_row: dict = None,
    db_path: str = None,
) -> Optional[dict]:
    """
    Run the free Gemini trade decider on one brief signal.

    Returns parsed decision dict with model_used, or None if blocked/failed.
    """
    strategy = load_strategy()
    ticker = str(signal.get("ticker", "")).upper()
    catalyst_date = extract_catalyst_date(signal, watchlist_row)
    days_to_cat = days_until(catalyst_date) if catalyst_date else "unknown"
    change_5d = get_change_5d_pct(ticker)
    info = get_ticker_info(ticker)

    guard = can_classify(headline=f"trade-decide:{ticker}:{signal.get('week_of')}", db_path=db_path)
    if not guard["allowed"]:
        print(f"  [DECIDER] {ticker}: blocked by cost guard — {guard['reason']}")
        return None

    if not GEMINI_API_KEY:
        print("  [DECIDER] GEMINI_API_KEY not set")
        return None

    prompt = TRADE_DECISION_PROMPT.format(
        ticker=ticker,
        research_direction=signal.get("direction", "skip"),
        research_conviction=signal.get("conviction", 0),
        thesis=(signal.get("thesis") or "")[:500],
        catalysts=json.dumps(signal.get("catalysts") or []),
        high_conviction=signal.get("high_conviction", False),
        current_price=current_price,
        change_5d_pct=change_5d if change_5d is not None else "unknown",
        days_to_catalyst=days_to_cat,
        market_cap_tier=info.get("market_cap_tier", "mid"),
        default_hold_through=strategy.get("default_hold_through_catalyst", False),
    )

    from google import genai

    client = genai.Client(api_key=GEMINI_API_KEY)
    preferred = os.getenv("TRADE_DECIDER_MODEL") or strategy.get("trade_decider_model")
    models = [preferred] + [m for m in get_model_chain() if m != preferred]

    response = None
    used_model = None
    import time as _time

    for model_name in models:
        for attempt in range(2):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config={"max_output_tokens": 250, "temperature": 0.1},
                )
                used_model = model_name
                break
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "quota" in err_str.lower():
                    break
                if "503" in err_str and attempt == 0:
                    _time.sleep(2)
                    continue
                break
        if used_model:
            break

    if response is None:
        print(f"  [DECIDER] {ticker}: all models failed")
        return None

    raw_text = (response.text or "").strip()
    json_text = raw_text
    if json_text.startswith("```"):
        lines = json_text.split("\n")
        json_text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError:
        print(f"  [DECIDER] {ticker}: JSON parse error — {raw_text[:150]}")
        return None

    input_tokens = len(prompt) // 4
    output_tokens = len(raw_text) // 4
    increment_llm_usage(used_model, input_tokens, output_tokens, db_path=db_path)

    # Normalize catalyst_date from model or our extraction
    if not parsed.get("catalyst_date") and catalyst_date:
        parsed["catalyst_date"] = catalyst_date
    parsed["model_used"] = used_model
    parsed["days_to_catalyst"] = days_to_cat if isinstance(days_to_cat, int) else None

    print(
        f"  [DECIDER] {ticker}: act={parsed.get('act')} dir={parsed.get('direction')} "
        f"tier={parsed.get('size_tier')} model={used_model} — {parsed.get('rationale', '')[:80]}"
    )
    return parsed


def size_tier_multiplier(size_tier: str) -> float:
    multipliers = load_strategy().get("size_tier_multipliers") or {}
    return float(multipliers.get(str(size_tier or "none").lower(), 0.0))
