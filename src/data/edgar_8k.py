"""
SEC EDGAR 8-K Filing Fetcher

Polls SEC EDGAR for new 8-K filings for each ticker in the watchlist.
Uses the data.sec.gov/submissions/ JSON API (no auth required, just User-Agent).

Rate limit: SEC asks for max 10 requests/second. We add 0.15s delay between calls.
"""

import os
import time
import yaml
import requests
from datetime import datetime, timedelta
from typing import Optional

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from src.db.storage import insert_news_event, get_recent_news

SEC_HEADERS = {
    'User-Agent': 'Rohan Skaria (rohan.skaria@email.com)',
    'Accept-Encoding': 'gzip, deflate',
    'Host': 'data.sec.gov'
}

WATCHLIST_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'watchlist.yaml')

# SEC asks for max 10 req/s -- we stay well under
REQUEST_DELAY = 0.15


def load_watchlist(path: str = None) -> dict:
    """Load the watchlist YAML config."""
    p = path or WATCHLIST_PATH
    with open(p, 'r') as f:
        return yaml.safe_load(f)


def fetch_company_filings(cik: str) -> Optional[dict]:
    """Fetch the full filing history JSON for a given CIK from SEC EDGAR."""
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    try:
        resp = requests.get(url, headers=SEC_HEADERS, timeout=15)
        if resp.status_code == 200:
            return resp.json()
        else:
            print(f"  [EDGAR] Error fetching CIK {cik}: HTTP {resp.status_code}")
            return None
    except Exception as e:
        print(f"  [EDGAR] Exception fetching CIK {cik}: {e}")
        return None


def extract_8k_filings(data: dict, since_days: int = 7) -> list:
    """
    Extract 8-K filings from the EDGAR submissions JSON.
    Only returns filings from the last `since_days` days.
    """
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accession_numbers = recent.get("accessionNumber", [])
    descriptions = recent.get("primaryDocDescription", [])
    primary_docs = recent.get("primaryDocument", [])

    cutoff = (datetime.utcnow() - timedelta(days=since_days)).strftime("%Y-%m-%d")
    company_name = data.get("name", "Unknown")

    filings = []
    for i, form in enumerate(forms):
        if form != "8-K":
            continue
        filing_date = dates[i] if i < len(dates) else ""
        if filing_date < cutoff:
            continue  # too old

        accession = accession_numbers[i] if i < len(accession_numbers) else ""
        desc = descriptions[i] if i < len(descriptions) else "8-K"
        primary_doc = primary_docs[i] if i < len(primary_docs) else ""

        # Build the filing URL
        accession_clean = accession.replace("-", "")
        filing_url = (
            f"https://www.sec.gov/Archives/edgar/data/"
            f"{data.get('cik', '').lstrip('0')}/{accession_clean}/{primary_doc}"
        ) if primary_doc else ""

        filings.append({
            "date": filing_date,
            "accession_number": accession,
            "description": desc,
            "filing_url": filing_url,
            "company_name": company_name,
        })

    return filings


def fetch_filing_text(url: str, max_chars: int = 5000) -> str:
    """
    Fetch the actual text content of an 8-K filing.
    Truncates to max_chars to keep LLM costs down.
    """
    if not url:
        return ""
    try:
        headers = {
            'User-Agent': 'Rohan Skaria (rohan.skaria@email.com)',
            'Accept-Encoding': 'gzip, deflate',
        }
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            text = resp.text
            import re
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()
            return text[:max_chars]
        return ""
    except Exception:
        return ""


def fetch_8k_items(cik: str, accession: str) -> str:
    """
    Fetch the 8-K filing index to extract the item descriptions
    (e.g., 'Item 2.02 Results of Operations', 'Item 8.01 Other Events').
    These items are much more informative than the bare '8-K' description.
    """
    accession_clean = accession.replace("-", "")
    cik_clean = cik.lstrip("0")
    index_url = f"https://www.sec.gov/Archives/edgar/data/{cik_clean}/{accession_clean}/"

    try:
        headers = {
            'User-Agent': 'Rohan Skaria (rohan.skaria@email.com)',
            'Accept-Encoding': 'gzip, deflate',
        }
        resp = requests.get(index_url, headers=headers, timeout=15)
        if resp.status_code == 200:
            import re
            # Look for 8-K item references in the filing index page
            items = re.findall(r'Item\s+\d+\.\d+[^<"]*', resp.text, re.IGNORECASE)
            if items:
                # Deduplicate and clean
                seen = set()
                unique_items = []
                for item in items:
                    cleaned = item.strip().rstrip('.')
                    if cleaned not in seen:
                        seen.add(cleaned)
                        unique_items.append(cleaned)
                return "; ".join(unique_items[:5])  # max 5 items
        return ""
    except Exception:
        return ""


def get_ticker_list(watchlist: dict) -> list:
    """
    Extract ticker list from watchlist config.
    Handles both old format (tickers dict) and new format (watchlists.core_biotech list).
    """
    # New format: watchlists.core_biotech is a list of dicts
    if "watchlists" in watchlist:
        core = watchlist.get("watchlists", {}).get("core_biotech", [])
        return core
    # Old format: tickers is a dict
    elif "tickers" in watchlist:
        return [{"ticker": k, **v} for k, v in watchlist.get("tickers", {}).items()]
    return []


def poll_all_tickers(since_days: int = 7, db_path: str = None, fetch_text: bool = False) -> list:
    """
    Poll EDGAR for all tickers in the watchlist.
    Returns list of newly inserted news events.
    """
    watchlist = load_watchlist()
    ticker_list = get_ticker_list(watchlist)
    new_events = []

    for info in ticker_list:
        ticker = info.get("ticker", "")
        cik = info.get("cik", "")
        if not cik or not ticker:
            print(f"  [EDGAR] Missing ticker or CIK, skipping: {info}")
            continue

        print(f"  [EDGAR] Fetching filings for {ticker} (CIK: {cik})...")
        data = fetch_company_filings(cik)
        if not data:
            continue

        filings = extract_8k_filings(data, since_days=since_days)
        print(f"  [EDGAR] Found {len(filings)} recent 8-K filings for {ticker}")

        for filing in filings:
            # Try to get 8-K item descriptions for a more informative headline
            items_desc = fetch_8k_items(cik, filing["accession_number"])
            time.sleep(REQUEST_DELAY)

            if items_desc:
                headline = f"{ticker}: {filing['company_name']} 8-K ({items_desc}) filed {filing['date']}"
            else:
                headline = f"{ticker}: {filing['company_name']} 8-K filed {filing['date']} - {filing['description']}"

            raw_text = ""
            if fetch_text and filing["filing_url"]:
                raw_text = fetch_filing_text(filing["filing_url"])
                time.sleep(REQUEST_DELAY)

            news_id = insert_news_event(
                ticker=ticker,
                headline=headline,
                filing_type="8-K",
                accession_number=filing["accession_number"],
                filing_url=filing["filing_url"],
                raw_text=raw_text,
                timestamp=filing["date"],
                db_path=db_path,
            )

            if news_id:
                new_events.append({
                    "news_id": news_id,
                    "ticker": ticker,
                    "headline": headline,
                    "date": filing["date"],
                    "accession": filing["accession_number"],
                })
                print(f"    -> NEW: {headline}")
            # else: duplicate, already in DB

        time.sleep(REQUEST_DELAY)

    return new_events


if __name__ == "__main__":
    from src.db.schema import init_db
    init_db()
    print("\nPolling EDGAR for recent 8-K filings...")
    events = poll_all_tickers(since_days=30, fetch_text=False)
    print(f"\nTotal new events: {len(events)}")
    for e in events:
        print(f"  [{e['date']}] {e['headline']}")
