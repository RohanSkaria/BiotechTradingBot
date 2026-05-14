"""
Synthetic Backtest

Pulls the last 2 years of 8-K filings from SEC EDGAR and "replays" them through
the Gemini classifier to see historical win rates and strategy performance.

This is a "synthetic" backtest because we're running current classification
logic against historical filings, not live-trading them.

Usage:
    python src/backtest/synthetic_backtest.py --years 2 --max-filings 100
"""

import os
import sys
import time
import json
import argparse
from datetime import datetime, timedelta
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from src.db.schema import init_db, get_connection
from src.data.edgar_8k import (
    load_watchlist, get_ticker_list, fetch_company_filings,
    extract_8k_filings, fetch_filing_text, fetch_8k_items, REQUEST_DELAY
)
from src.analysis.keyword_filter import filter_headline, should_classify
from src.analysis.gemini_classifier import classify_headline
from src.backtest.price_data import get_price_around_event
from src.backtest.evaluator import evaluate_signal
from src.backtest.report import generate_report, print_report


def fetch_historical_filings(years: int = 2, max_per_ticker: int = 50) -> list:
    """
    Fetch historical 8-K filings from SEC EDGAR for all tickers in watchlist.
    
    Args:
        years: How many years back to look (default: 2)
        max_per_ticker: Max filings per ticker to avoid API abuse
    
    Returns:
        List of filing dicts with: ticker, date, headline, accession, filing_url
    """
    watchlist = load_watchlist()
    ticker_list = get_ticker_list(watchlist)
    
    since_days = years * 365
    all_filings = []
    
    print(f"\n{'='*60}")
    print(f"FETCHING HISTORICAL 8-K FILINGS ({years} years)")
    print(f"{'='*60}")
    
    for info in ticker_list:
        ticker = info.get("ticker", "")
        cik = info.get("cik", "")
        name = info.get("name", ticker)
        
        if not cik or not ticker:
            continue
        
        print(f"\n  [{ticker}] Fetching from SEC EDGAR (CIK: {cik})...")
        
        data = fetch_company_filings(cik)
        if not data:
            print(f"    -> Failed to fetch company data")
            continue
        
        # Extract all 8-K filings (not just recent)
        filings = extract_8k_filings(data, since_days=since_days)
        filings = filings[:max_per_ticker]  # Limit per ticker
        
        print(f"    -> Found {len(filings)} 8-K filings")
        
        for filing in filings:
            # Get item descriptions for better headlines
            items_desc = fetch_8k_items(cik, filing["accession_number"])
            time.sleep(REQUEST_DELAY)
            
            if items_desc:
                headline = f"{ticker}: {name} 8-K ({items_desc}) filed {filing['date']}"
            else:
                headline = f"{ticker}: {name} 8-K filed {filing['date']} - {filing['description']}"
            
            all_filings.append({
                "ticker": ticker,
                "name": name,
                "date": filing["date"],
                "headline": headline,
                "accession": filing["accession_number"],
                "filing_url": filing["filing_url"],
                "market_cap_tier": info.get("market_cap_tier", "mid"),
                "volatility": info.get("volatility", "medium"),
            })
        
        time.sleep(REQUEST_DELAY)
    
    # Sort by date (oldest first for chronological replay)
    all_filings.sort(key=lambda x: x["date"])
    
    print(f"\n  Total historical filings: {len(all_filings)}")
    return all_filings


def run_synthetic_backtest(
    filings: list,
    fetch_text: bool = False,
    max_classify: int = 50,
    skip_filter: bool = False,
    verbose: bool = True,
) -> dict:
    """
    Run the synthetic backtest: classify historical filings and compare to actual price moves.
    
    Args:
        filings: List of historical filing dicts
        fetch_text: Whether to fetch full filing text (slower, more accurate)
        max_classify: Max filings to classify (to preserve API quota)
        skip_filter: Skip keyword filter, classify all filings (for testing)
        verbose: Print progress
    
    Returns:
        Dict with results: classifications, evaluations, summary stats
    """
    print(f"\n{'='*60}")
    print(f"RUNNING SYNTHETIC BACKTEST")
    print(f"{'='*60}")
    print(f"  Filings to process: {len(filings)}")
    print(f"  Max classifications: {max_classify}")
    print(f"  Fetch filing text: {fetch_text}")
    print(f"  Skip keyword filter: {skip_filter}")
    
    classifications = []
    evaluations = []
    skipped_filter = 0
    skipped_api = 0
    classified_count = 0
    
    for i, filing in enumerate(filings):
        ticker = filing["ticker"]
        headline = filing["headline"]
        filing_date = filing["date"]
        
        if verbose and i % 10 == 0:
            print(f"\n  Processing {i+1}/{len(filings)}...")
        
        # Step 1: Keyword filter (can be skipped for testing)
        filter_result = filter_headline(headline)
        
        # If headline doesn't pass, try with filing text
        if not should_classify(filter_result) and fetch_text:
            text = fetch_filing_text(filing["filing_url"], max_chars=2000)
            time.sleep(REQUEST_DELAY)
            if text:
                filter_result = filter_headline(text)
        
        if not skip_filter and not should_classify(filter_result):
            skipped_filter += 1
            continue
        
        # Step 2: Classify with Gemini (respect quota)
        if classified_count >= max_classify:
            skipped_api += 1
            continue
        
        result = classify_headline(headline)
        time.sleep(0.5)  # Rate limit Gemini calls
        
        if not result:
            skipped_api += 1
            continue
        
        classified_count += 1
        
        classification = {
            "ticker": ticker,
            "date": filing_date,
            "headline": headline,
            "category": result.get("category", "Other"),
            "sentiment": result.get("sentiment", "Neutral"),
            "confidence": result.get("confidence", 0),
            "affected_tickers": result.get("affected_tickers", []),
            "model_used": result.get("model_used", ""),
            "filter_score": filter_result.score,
            "filter_direction": filter_result.direction,
        }
        classifications.append(classification)
        
        if verbose:
            print(f"    [{filing_date}] {ticker}: {result.get('sentiment')} "
                  f"({result.get('confidence')}%) - {result.get('category')}")
        
        # Step 3: Evaluate against actual price movement
        evaluation = evaluate_signal(
            ticker=ticker,
            event_date=filing_date,
            predicted_sentiment=result.get("sentiment", "Neutral"),
            window="1d",
        )
        
        if evaluation:
            evaluation["headline"] = headline
            evaluation["category"] = result.get("category")
            evaluation["confidence"] = result.get("confidence")
            evaluations.append(evaluation)
            
            correct_str = "CORRECT" if evaluation["correct"] else "WRONG"
            if verbose:
                print(f"      -> Actual 1d return: {evaluation['actual_return_pct']:.2f}% "
                      f"({correct_str})")
    
    # Generate summary
    summary = {
        "total_filings": len(filings),
        "skipped_filter": skipped_filter,
        "skipped_api": skipped_api,
        "classified": classified_count,
        "evaluated": len(evaluations),
    }
    
    return {
        "classifications": classifications,
        "evaluations": evaluations,
        "summary": summary,
    }


def print_backtest_results(results: dict) -> None:
    """Pretty-print the synthetic backtest results."""
    summary = results["summary"]
    evaluations = results["evaluations"]
    classifications = results["classifications"]
    
    print(f"\n{'='*60}")
    print(f"SYNTHETIC BACKTEST RESULTS")
    print(f"{'='*60}")
    
    print(f"\n--- Filing Analysis ---")
    print(f"  Total filings scanned:     {summary['total_filings']}")
    print(f"  Skipped (low filter score): {summary['skipped_filter']}")
    print(f"  Skipped (API quota):        {summary['skipped_api']}")
    print(f"  Classified by Gemini:       {summary['classified']}")
    print(f"  Evaluated vs price:         {summary['evaluated']}")
    
    if evaluations:
        # Generate standard report
        report = generate_report(evaluations)
        if report:
            print_report(report)
        
        # Additional breakdown by ticker
        print(f"\n--- Performance by Ticker ---")
        by_ticker = {}
        for e in evaluations:
            t = e["ticker"]
            if t not in by_ticker:
                by_ticker[t] = {"correct": 0, "total": 0, "pnl": 0}
            by_ticker[t]["total"] += 1
            if e["correct"]:
                by_ticker[t]["correct"] += 1
            by_ticker[t]["pnl"] += e["simulated_pnl_pct"]
        
        print(f"  {'Ticker':<8} {'Signals':>8} {'Accuracy':>10} {'Avg P&L':>10}")
        print(f"  {'-'*8} {'-'*8} {'-'*10} {'-'*10}")
        for ticker, stats in sorted(by_ticker.items()):
            acc = stats["correct"] / stats["total"] * 100 if stats["total"] > 0 else 0
            avg_pnl = stats["pnl"] / stats["total"] if stats["total"] > 0 else 0
            print(f"  {ticker:<8} {stats['total']:>8} {acc:>9.1f}% {avg_pnl:>9.2f}%")
        
        # Breakdown by category
        print(f"\n--- Performance by Category ---")
        by_category = {}
        for e in evaluations:
            cat = e.get("category", "Other")
            if cat not in by_category:
                by_category[cat] = {"correct": 0, "total": 0, "pnl": 0}
            by_category[cat]["total"] += 1
            if e["correct"]:
                by_category[cat]["correct"] += 1
            by_category[cat]["pnl"] += e["simulated_pnl_pct"]
        
        print(f"  {'Category':<25} {'Signals':>8} {'Accuracy':>10} {'Avg P&L':>10}")
        print(f"  {'-'*25} {'-'*8} {'-'*10} {'-'*10}")
        for cat, stats in sorted(by_category.items(), key=lambda x: x[1]["total"], reverse=True):
            acc = stats["correct"] / stats["total"] * 100 if stats["total"] > 0 else 0
            avg_pnl = stats["pnl"] / stats["total"] if stats["total"] > 0 else 0
            print(f"  {cat:<25} {stats['total']:>8} {acc:>9.1f}% {avg_pnl:>9.2f}%")
    else:
        print(f"\n  No evaluations to report. Try increasing --max-classify or --years.")
    
    # Save results to file
    output_path = os.path.join(
        os.path.dirname(__file__), '..', '..', 'data', 'synthetic_backtest_results.json'
    )
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  Results saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Synthetic Backtest for Biotech Trading Bot")
    parser.add_argument("--years", type=int, default=2, help="Years of history to fetch (default: 2)")
    parser.add_argument("--max-filings", type=int, default=100, help="Max filings per ticker (default: 100)")
    parser.add_argument("--max-classify", type=int, default=50, help="Max Gemini classifications (default: 50)")
    parser.add_argument("--fetch-text", action="store_true", help="Fetch full filing text (slower)")
    parser.add_argument("--skip-filter", action="store_true", help="Skip keyword filter (classify all)")
    parser.add_argument("--quiet", action="store_true", help="Reduce output verbosity")
    args = parser.parse_args()
    
    # Initialize database (for potential storage)
    init_db()
    
    # Fetch historical filings
    filings = fetch_historical_filings(
        years=args.years,
        max_per_ticker=args.max_filings,
    )
    
    if not filings:
        print("No filings found. Check watchlist configuration.")
        return
    
    # Run backtest
    results = run_synthetic_backtest(
        filings=filings,
        fetch_text=args.fetch_text,
        max_classify=args.max_classify,
        skip_filter=args.skip_filter,
        verbose=not args.quiet,
    )
    
    # Print results
    print_backtest_results(results)


if __name__ == "__main__":
    main()
