"""
Post-catalyst exit study (event study).

Answers "what is the optimal exit after a biotech catalyst?" empirically, on
OUR OWN watchlist, instead of guessing. For every dated catalyst we can find in
the DB, it measures BENCHMARK-ADJUSTED returns (stock minus XBI, to strip out
biotech-sector beta) at several horizons after the event:

    open-gap, +1d, +3d, +5d, +10d, +20d

and segments by catalyst category (FDA Decision / Clinical Trial / Offering /
Other) and by the sign of the day-1 reaction. The shape of the resulting drift
curve tells you where the post-event edge persists vs. dies.

IMPORTANT — small samples: biotech catalysts are low-frequency. With only a
handful of events per bucket, results are DIRECTIONAL, not statistically robust.
The report prints N for every bucket and flags thin buckets. Treat the output as
a monitoring tool that sharpens as catalysts accumulate; anchor actual exit
rules on the peer-reviewed literature until N is large.

Usage:
    python -m src.backtest.exit_study
    python -m src.backtest.exit_study --benchmark SPY --min-n 5
"""

import os
import sys
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean, median
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.db.schema import get_connection, is_postgres

HORIZONS = ["gap", "1d", "3d", "5d", "10d", "20d"]
HORIZON_DAYS = {"1d": 1, "3d": 3, "5d": 5, "10d": 10, "20d": 20}
DEFAULT_BENCHMARK = "XBI"  # SPDR S&P Biotech ETF: equal-weight biotech beta
RESULTS_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "exit_study_results.json")


def categorize(event_text: str, direction: str = "") -> str:
    """Bucket a catalyst by its text into a coarse category."""
    t = (event_text or "").lower()
    if any(k in t for k in ["pdufa", "fda approval", "approval", "crl", "complete response",
                            "advisory committee", "adcom", "marketing authorization", "chmp"]):
        return "FDA Decision"
    if any(k in t for k in ["phase 1", "phase 2", "phase 3", "phase i", "phase ii", "phase iii",
                            "topline", "readout", "primary endpoint", "trial", "data"]):
        return "Clinical Trial"
    if any(k in t for k in ["offering", "dilut", "registration", "raise", "financing"]):
        return "Offering"
    if any(k in t for k in ["acqui", "merger", "buyout", "licensing", "deal", "partnership"]):
        return "M&A/Deal"
    return "Other"


def _parse_date(text) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"(20\d{2}-\d{2}-\d{2})", str(text))
    return m.group(1) if m else None


def load_events_from_db() -> list:
    """
    Build a deduped event set from weekly_briefings dated catalysts.

    Returns list of dicts: {ticker, date, category, direction, event}.
    """
    conn = get_connection()
    if is_postgres():
        cur = conn.cursor()
        cur.execute("SELECT ticker, week_of, direction, conviction, catalysts FROM weekly_briefings")
        rows = [dict(r) for r in cur.fetchall()]
    else:
        rows = [dict(r) for r in conn.execute(
            "SELECT ticker, week_of, direction, conviction, catalysts FROM weekly_briefings"
        ).fetchall()]
    conn.close()

    seen = {}
    for r in rows:
        cats = r.get("catalysts")
        if isinstance(cats, str):
            try:
                cats = json.loads(cats)
            except json.JSONDecodeError:
                cats = []
        for c in (cats or []):
            if not isinstance(c, dict):
                continue
            date = _parse_date(c.get("date")) or _parse_date(c.get("event"))
            if not date:
                continue
            ticker = str(r["ticker"]).upper()
            key = (ticker, date)
            if key in seen:
                continue
            seen[key] = {
                "ticker": ticker,
                "date": date,
                "category": categorize(c.get("event", ""), r.get("direction", "")),
                "direction": (r.get("direction") or "").lower(),
                "event": c.get("event", ""),
            }
    return sorted(seen.values(), key=lambda e: e["date"])


def _fetch_closes(ticker: str, start: str, end: str):
    """Return (dates, opens, closes) lists via yfinance, or None."""
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=True)
        if hist is None or hist.empty:
            return None
        hist.index = hist.index.tz_localize(None)
        return list(hist.index), list(hist["Open"]), list(hist["Close"])
    except Exception:
        return None


def _pct(a: float, b: float) -> Optional[float]:
    if a is None or b is None or a == 0:
        return None
    return (b - a) / a * 100.0


def measure_event(event: dict, benchmark: str) -> Optional[dict]:
    """
    Compute benchmark-adjusted returns at each horizon for one event.
    Abnormal return = stock_return - benchmark_return over the same window.
    """
    date = event["date"]
    event_dt = datetime.strptime(date, "%Y-%m-%d")
    start = (event_dt - timedelta(days=10)).strftime("%Y-%m-%d")
    end = (event_dt + timedelta(days=40)).strftime("%Y-%m-%d")

    stock = _fetch_closes(event["ticker"], start, end)
    bench = _fetch_closes(benchmark, start, end)
    if not stock or not bench:
        return None

    s_dates, s_opens, s_closes = stock
    b_dates, b_opens, b_closes = bench

    # Index of the event close = last trading day on/before the catalyst date
    eidx = None
    for i, d in enumerate(s_dates):
        if d.date() <= event_dt.date():
            eidx = i
        else:
            break
    if eidx is None or eidx + 1 >= len(s_closes):
        return None

    def bench_at(target_date):
        """Closest benchmark close index to a given stock trading date."""
        for j, d in enumerate(b_dates):
            if d.date() == target_date.date():
                return j
        return None

    e_close = s_closes[eidx]
    b_eidx = bench_at(s_dates[eidx])
    if b_eidx is None or b_eidx + 1 >= len(b_closes):
        return None
    b_e_close = b_closes[b_eidx]

    abn = {}

    # Open gap: event close -> next session open
    s_gap = _pct(e_close, s_opens[eidx + 1])
    b_gap = _pct(b_e_close, b_opens[b_eidx + 1]) if b_eidx + 1 < len(b_opens) else None
    if s_gap is not None and b_gap is not None:
        abn["gap"] = round(s_gap - b_gap, 2)

    for label, n in HORIZON_DAYS.items():
        if eidx + n < len(s_closes) and b_eidx + n < len(b_closes):
            s_ret = _pct(e_close, s_closes[eidx + n])
            b_ret = _pct(b_e_close, b_closes[b_eidx + n])
            if s_ret is not None and b_ret is not None:
                abn[label] = round(s_ret - b_ret, 2)

    if not abn:
        return None

    day1_sign = "up" if (abn.get("1d", 0) or 0) >= 0 else "down"
    return {**event, "abnormal": abn, "day1_sign": day1_sign}


def _agg(values: list) -> dict:
    vals = [v for v in values if v is not None]
    if not vals:
        return {"n": 0}
    return {
        "n": len(vals),
        "mean": round(mean(vals), 2),
        "median": round(median(vals), 2),
        "min": round(min(vals), 2),
        "max": round(max(vals), 2),
    }


def run_study(benchmark: str = DEFAULT_BENCHMARK, min_n: int = 5) -> dict:
    events = load_events_from_db()
    print(f"  [STUDY] {len(events)} unique dated catalyst(s) found in DB")

    measured = []
    for e in events:
        m = measure_event(e, benchmark)
        if m:
            measured.append(m)
    print(f"  [STUDY] {len(measured)} event(s) had usable post-event price data "
          f"(benchmark-adjusted vs {benchmark})")

    if not measured:
        print("  [STUDY] No measurable events yet — harness will sharpen as catalysts accrue.")
        return {"events": 0}

    overall = {h: _agg([m["abnormal"].get(h) for m in measured]) for h in HORIZONS}

    by_cat = defaultdict(lambda: defaultdict(list))
    for m in measured:
        for h in HORIZONS:
            by_cat[m["category"]][h].append(m["abnormal"].get(h))
    cat_stats = {c: {h: _agg(v[h]) for h in HORIZONS} for c, v in by_cat.items()}

    by_sign = defaultdict(lambda: defaultdict(list))
    for m in measured:
        for h in HORIZONS:
            by_sign[m["day1_sign"]][h].append(m["abnormal"].get(h))
    sign_stats = {s: {h: _agg(v[h]) for h in HORIZONS} for s, v in by_sign.items()}

    result = {
        "benchmark": benchmark,
        "events_measured": len(measured),
        "min_n": min_n,
        "overall": overall,
        "by_category": cat_stats,
        "by_day1_sign": sign_stats,
        "events": [
            {"ticker": m["ticker"], "date": m["date"], "category": m["category"],
             "day1_sign": m["day1_sign"], "abnormal": m["abnormal"]}
            for m in measured
        ],
    }

    os.makedirs(os.path.dirname(RESULTS_PATH), exist_ok=True)
    with open(RESULTS_PATH, "w") as f:
        json.dump(result, f, indent=2, default=str)

    _print_study(result)
    return result


def _row(label, stats):
    cells = []
    for h in HORIZONS:
        s = stats.get(h, {})
        if s.get("n"):
            cells.append(f"{s['mean']:+6.1f}%(n={s['n']})")
        else:
            cells.append(f"{'--':>10s}")
    return f"  {label:<18s} " + " ".join(f"{c:>13s}" for c in cells)


def _print_study(r: dict) -> None:
    print("\n" + "=" * 96)
    print(f"POST-CATALYST EXIT STUDY  (benchmark-adjusted vs {r['benchmark']}, "
          f"{r['events_measured']} events)")
    print("=" * 96)
    header = "  " + f"{'bucket':<18s} " + " ".join(f"{h:>13s}" for h in HORIZONS)
    print(header)
    print("  " + "-" * 92)
    print(_row("ALL", r["overall"]))
    print("\n  By catalyst category:")
    for cat, stats in sorted(r["by_category"].items()):
        print(_row(cat, stats))
    print("\n  By day-1 reaction sign (tests 'momentum vs reversion'):")
    for sign, stats in sorted(r["by_day1_sign"].items()):
        print(_row(f"day1 {sign}", stats))

    thin = r["events_measured"] < r["min_n"] * 2
    print("\n  " + ("!! SMALL SAMPLE — directional only, not statistically robust. "
                    "Anchor rules on the literature until N grows."
                    if thin else "Sample is modest; treat as directional."))
    print(f"  Full results: {os.path.relpath(RESULTS_PATH)}")
    print("=" * 96 + "\n")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Post-catalyst exit study (event study).")
    parser.add_argument("--benchmark", default=DEFAULT_BENCHMARK, help="Benchmark ticker (default XBI)")
    parser.add_argument("--min-n", type=int, default=5, help="Min events per bucket to trust")
    args = parser.parse_args()
    run_study(benchmark=args.benchmark, min_n=args.min_n)
