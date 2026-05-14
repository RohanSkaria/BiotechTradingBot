"""
Clinical Trial Tracker

Monitors ClinicalTrials.gov for status changes on tracked NCT IDs.
Key signals:
- RECRUITING -> ACTIVE_NOT_RECRUITING: Results coming soon
- ACTIVE_NOT_RECRUITING -> COMPLETED: Expect 8-K filing

Runs every 6 hours to catch status changes before the market reacts.
"""

import os
import json
import requests
from datetime import datetime, timezone
from typing import Optional, List, Dict

from dotenv import load_dotenv
load_dotenv()

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from db.schema import get_connection, is_postgres
from alerts.discord import send_message, send_system_alert

# ClinicalTrials.gov API v2 base URL
CT_API_BASE = "https://clinicaltrials.gov/api/v2/studies"

# Sponsor name mapping for watchlist tickers
TICKER_TO_SPONSOR = {
    "LLY": "Eli Lilly",
    "VRTX": "Vertex Pharmaceuticals",
    "CRSP": "CRISPR Therapeutics",
    "REGN": "Regeneron",
    "AMGN": "Amgen",
    "GILD": "Gilead Sciences",
}

# Status changes that trigger alerts
HIGH_ALERT_TRANSITIONS = {
    ("RECRUITING", "ACTIVE_NOT_RECRUITING"): "Trial enrollment complete - results expected soon",
    ("ACTIVE_NOT_RECRUITING", "COMPLETED"): "Trial completed - 8-K filing imminent",
    ("RECRUITING", "COMPLETED"): "Trial completed - 8-K filing imminent",
    ("SUSPENDED", "TERMINATED"): "Trial terminated - potential negative signal",
    ("RECRUITING", "SUSPENDED"): "Trial suspended - potential safety issue",
    ("RECRUITING", "TERMINATED"): "Trial terminated - potential failure",
}


def get_tracked_trials() -> List[Dict]:
    """Get all trials being tracked from the database."""
    if not is_postgres():
        return []
    
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT nct_id, ticker, sponsor_name, trial_name, phase, 
               status, last_known_status, alert_sent
        FROM clinical_trials
        ORDER BY ticker, nct_id
    """)
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def fetch_trial_status(nct_id: str) -> Optional[Dict]:
    """
    Fetch current status of a trial from ClinicalTrials.gov API.
    
    Returns dict with: status, phase, title, primary_completion_date
    """
    try:
        url = f"{CT_API_BASE}/{nct_id}"
        params = {
            "fields": "NCTId,BriefTitle,OverallStatus,Phase,PrimaryCompletionDate,LastUpdatePostDate"
        }
        
        response = requests.get(url, params=params, timeout=15)
        
        if response.status_code == 404:
            print(f"  [CT] {nct_id}: Not found (may be invalid ID)")
            return None
        
        response.raise_for_status()
        data = response.json()
        
        protocol = data.get('protocolSection', {})
        status_module = protocol.get('statusModule', {})
        id_module = protocol.get('identificationModule', {})
        design_module = protocol.get('designModule', {})
        
        return {
            'nct_id': nct_id,
            'status': status_module.get('overallStatus', 'UNKNOWN'),
            'title': id_module.get('briefTitle', ''),
            'phase': ', '.join(design_module.get('phases', ['Unknown'])),
            'primary_completion_date': status_module.get('primaryCompletionDateStruct', {}).get('date'),
            'last_update': status_module.get('lastUpdatePostDateStruct', {}).get('date'),
        }
        
    except requests.exceptions.RequestException as e:
        print(f"  [CT] {nct_id}: API error - {e}")
        return None
    except Exception as e:
        print(f"  [CT] {nct_id}: Parse error - {e}")
        return None


def update_trial_status(nct_id: str, new_status: str, completion_date: str = None) -> bool:
    """Update the trial status in the database."""
    if not is_postgres():
        return False
    
    conn = get_connection()
    cur = conn.cursor()
    
    # Handle incomplete date formats from ClinicalTrials.gov (e.g., "2026-01" -> "2026-01-01")
    parsed_date = None
    if completion_date:
        try:
            if len(completion_date) == 7:  # YYYY-MM format
                parsed_date = completion_date + "-01"
            elif len(completion_date) == 4:  # YYYY format
                parsed_date = completion_date + "-01-01"
            else:
                parsed_date = completion_date
        except Exception:
            parsed_date = None
    
    try:
        cur.execute("""
            UPDATE clinical_trials
            SET last_known_status = status,
                status = %s,
                primary_completion_date = %s,
                last_checked = CURRENT_TIMESTAMP
            WHERE nct_id = %s
        """, (new_status, parsed_date, nct_id))
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        print(f"  [CT] DB update error for {nct_id}: {e}")
        return False
    finally:
        conn.close()


def mark_alert_sent(nct_id: str) -> None:
    """Mark that an alert has been sent for this trial."""
    if not is_postgres():
        return
    
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE clinical_trials 
        SET alert_sent = TRUE 
        WHERE nct_id = %s
    """, (nct_id,))
    conn.commit()
    conn.close()


def reset_alert_flag(nct_id: str) -> None:
    """Reset the alert flag when status changes again."""
    if not is_postgres():
        return
    
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE clinical_trials 
        SET alert_sent = FALSE 
        WHERE nct_id = %s
    """, (nct_id,))
    conn.commit()
    conn.close()


def send_trial_alert(trial: Dict, old_status: str, new_status: str, reason: str) -> bool:
    """Send a Discord alert for a trial status change."""
    ticker = trial.get('ticker', 'N/A')
    nct_id = trial.get('nct_id', 'N/A')
    trial_name = trial.get('trial_name', 'Unknown trial')
    
    # Determine emoji based on signal type
    if "completed" in reason.lower() or "results" in reason.lower():
        emoji = "🚀"
    elif "terminated" in reason.lower() or "suspended" in reason.lower():
        emoji = "🔴"
    else:
        emoji = "⚠️"
    
    content = (
        f"{emoji} **Clinical Trial Alert: ${ticker}**\n\n"
        f"**Trial:** {nct_id}\n"
        f"> {trial_name[:200]}\n\n"
        f"**Status Change:** `{old_status}` → `{new_status}`\n"
        f"**Signal:** {reason}\n\n"
        f"_Monitor for upcoming 8-K filing._\n"
        f"`{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}`"
    )
    
    return send_message(content, username="Clinical Tracker")


def check_trial_status() -> Dict:
    """
    Main function: Check all tracked trials for status changes.
    
    Returns dict with counts of checked, changed, alerted trials.
    """
    print("=" * 60)
    print("🔬 CLINICAL TRACKER - Status Check")
    print("=" * 60)
    print(f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print()
    
    trials = get_tracked_trials()
    
    if not trials:
        print("  [CT] No trials being tracked")
        return {'checked': 0, 'changed': 0, 'alerted': 0}
    
    print(f"Checking {len(trials)} tracked trials...")
    print()
    
    results = {
        'checked': 0,
        'changed': 0,
        'alerted': 0,
        'errors': 0,
        'changes': []
    }
    
    for trial in trials:
        nct_id = trial['nct_id']
        ticker = trial['ticker']
        old_status = trial['status']
        
        print(f"  [{ticker}] {nct_id}...", end=" ")
        
        # Fetch current status from API
        api_data = fetch_trial_status(nct_id)
        
        if not api_data:
            results['errors'] += 1
            print("ERROR")
            continue
        
        results['checked'] += 1
        new_status = api_data['status']
        
        # Check for status change
        if old_status != new_status and old_status != 'UNKNOWN':
            results['changed'] += 1
            print(f"CHANGED: {old_status} → {new_status}")
            
            # Update database
            update_trial_status(nct_id, new_status, api_data.get('primary_completion_date'))
            reset_alert_flag(nct_id)
            
            # Check if this is a high-alert transition
            transition_key = (old_status, new_status)
            if transition_key in HIGH_ALERT_TRANSITIONS:
                reason = HIGH_ALERT_TRANSITIONS[transition_key]
                print(f"    🚨 HIGH ALERT: {reason}")
                
                if send_trial_alert(trial, old_status, new_status, reason):
                    mark_alert_sent(nct_id)
                    results['alerted'] += 1
                
                results['changes'].append({
                    'nct_id': nct_id,
                    'ticker': ticker,
                    'old_status': old_status,
                    'new_status': new_status,
                    'reason': reason
                })
        else:
            # Just update the timestamp and status if it was UNKNOWN
            if old_status == 'UNKNOWN':
                update_trial_status(nct_id, new_status, api_data.get('primary_completion_date'))
                print(f"INITIALIZED: {new_status}")
            else:
                # Update last_checked timestamp
                if is_postgres():
                    conn = get_connection()
                    cur = conn.cursor()
                    cur.execute("""
                        UPDATE clinical_trials SET last_checked = CURRENT_TIMESTAMP
                        WHERE nct_id = %s
                    """, (nct_id,))
                    conn.commit()
                    conn.close()
                print(f"OK ({new_status})")
    
    print()
    print("-" * 60)
    print(f"Summary: {results['checked']} checked, {results['changed']} changed, "
          f"{results['alerted']} alerts sent, {results['errors']} errors")
    print("=" * 60)
    
    return results


def discover_trials_for_sponsor(sponsor_name: str, ticker: str, max_results: int = 10) -> List[Dict]:
    """
    Discover NCT IDs for a sponsor from ClinicalTrials.gov.
    
    Returns list of trial dicts with nct_id, title, phase, status.
    """
    print(f"  Searching for {sponsor_name} ({ticker}) trials...")
    
    try:
        params = {
            "query.spons": sponsor_name,
            "filter.overallStatus": "RECRUITING,ACTIVE_NOT_RECRUITING,ENROLLING_BY_INVITATION",
            "fields": "NCTId,BriefTitle,Phase,OverallStatus,PrimaryCompletionDate",
            "pageSize": max_results
        }
        
        response = requests.get(CT_API_BASE, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        studies = data.get('studies', [])
        results = []
        
        for study in studies:
            protocol = study.get('protocolSection', {})
            id_module = protocol.get('identificationModule', {})
            status_module = protocol.get('statusModule', {})
            design_module = protocol.get('designModule', {})
            
            results.append({
                'nct_id': id_module.get('nctId'),
                'title': id_module.get('briefTitle', ''),
                'phase': ', '.join(design_module.get('phases', ['Unknown'])),
                'status': status_module.get('overallStatus', 'UNKNOWN'),
                'completion_date': status_module.get('primaryCompletionDateStruct', {}).get('date'),
            })
        
        print(f"    Found {len(results)} active trials")
        return results
        
    except Exception as e:
        print(f"    Error: {e}")
        return []


def add_trial_to_tracking(nct_id: str, ticker: str, sponsor_name: str, 
                          trial_name: str, phase: str) -> bool:
    """Add a new trial to tracking in the database."""
    if not is_postgres():
        return False
    
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        cur.execute("""
            INSERT INTO clinical_trials (nct_id, ticker, sponsor_name, trial_name, phase, status, last_known_status)
            VALUES (%s, %s, %s, %s, %s, 'UNKNOWN', 'UNKNOWN')
            ON CONFLICT (nct_id) DO NOTHING
        """, (nct_id, ticker, sponsor_name, trial_name, phase))
        conn.commit()
        inserted = cur.rowcount > 0
        return inserted
    except Exception as e:
        conn.rollback()
        print(f"  Error adding {nct_id}: {e}")
        return False
    finally:
        conn.close()


def discover_and_add_trials(tickers: List[str] = None) -> Dict:
    """
    Discover trials for watchlist tickers and add them to tracking.
    
    This is a monthly maintenance task to find new trials.
    """
    if tickers is None:
        tickers = list(TICKER_TO_SPONSOR.keys())
    
    print("=" * 60)
    print("🔍 CLINICAL TRIAL DISCOVERY")
    print("=" * 60)
    print()
    
    results = {'discovered': 0, 'added': 0}
    
    for ticker in tickers:
        sponsor = TICKER_TO_SPONSOR.get(ticker)
        if not sponsor:
            continue
        
        trials = discover_trials_for_sponsor(sponsor, ticker)
        results['discovered'] += len(trials)
        
        for trial in trials:
            if add_trial_to_tracking(
                nct_id=trial['nct_id'],
                ticker=ticker,
                sponsor_name=sponsor,
                trial_name=trial['title'],
                phase=trial['phase']
            ):
                results['added'] += 1
                print(f"    ✓ Added: {trial['nct_id']}")
    
    print()
    print(f"Discovery complete: {results['discovered']} found, {results['added']} new trials added")
    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Clinical Trial Tracker")
    parser.add_argument("--discover", action="store_true", help="Discover new trials for watchlist")
    parser.add_argument("--check", action="store_true", help="Check status of tracked trials")
    args = parser.parse_args()
    
    if args.discover:
        discover_and_add_trials()
    else:
        # Default: check status
        check_trial_status()
