"""
Weekly Scout - Monday 4AM Catalyst Detection

Queries Gemini to find upcoming Phase 3, PDUFA, and other biotech catalysts
for the watchlist tickers. Updates Neon database with priority flags and
sends a Discord summary.
"""

import os
import json
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

# Import database functions
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from db.schema import get_connection, is_postgres
from db.storage import get_watchlist_from_db
from alerts.discord import send_weekly_scout_report, send_system_alert

# Gemini model fallback chain (same as classifier)
GEMINI_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite", 
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
]

# Initialize Gemini client
client = None
try:
    api_key = os.getenv("GEMINI_API_KEY")
    if api_key:
        client = genai.Client(api_key=api_key)
except Exception as e:
    print(f"[SCOUT] Warning: Could not initialize Gemini client: {e}")


def get_watchlist_tickers() -> list:
    """Get list of tickers from watchlist (Neon DB or config file)."""
    if is_postgres():
        watchlist = get_watchlist_from_db()
        return [w['ticker'] for w in watchlist]
    else:
        # Fallback to config file
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'watchlist.yaml')
        try:
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
                return [t['ticker'] for t in config.get('watchlists', {}).get('core_biotech', [])]
        except Exception:
            return ['LLY', 'VRTX', 'CRSP', 'REGN', 'AMGN', 'GILD']


def get_catalyst_prompt(tickers: list, scan_date: datetime) -> str:
    """Build the Gemini prompt for catalyst detection."""
    ticker_list = ', '.join(tickers)
    date_str = scan_date.strftime('%B %d, %Y')
    end_date = (scan_date + timedelta(days=7)).strftime('%B %d, %Y')
    
    prompt = f"""You are a biotech analyst assistant. Today is {date_str}.

I need you to identify any upcoming FDA/biotech catalysts for these tickers in the next 7 days (until {end_date}):

Tickers: {ticker_list}

Look for:
1. PDUFA dates (FDA drug approval decisions)
2. Phase 3 trial readouts
3. Clinical hold updates
4. Priority Review designations
5. Advisory committee (AdCom) meetings
6. Breakthrough Therapy designations
7. Complete Response Letter (CRL) responses

For each catalyst found, provide:
- ticker: The stock ticker
- event: Brief description of the catalyst
- date: Expected date (be specific if known, or "February 2026" if approximate)
- priority: "high" if within 7 days, "medium" if within 30 days, "low" otherwise
- source: Where this information typically comes from (e.g., "FDA calendar", "company PR")

Return your response as valid JSON with this exact structure:
{{
    "catalysts": [
        {{
            "ticker": "REGN",
            "event": "RGX-121 PDUFA Decision for Hunter Syndrome",
            "date": "February 8, 2026",
            "priority": "high",
            "source": "FDA PDUFA calendar"
        }}
    ],
    "watchlist_status": [
        {{
            "ticker": "LLY",
            "catalyst": "Orforglipron Priority Review (March 2026)",
            "priority": "medium"
        }}
    ]
}}

Include ALL tickers in watchlist_status, even if they have no imminent catalysts (set catalyst to "No imminent catalysts" in that case).

Important: Only include real, verifiable catalysts. Do not make up events. If you're unsure about a specific date, indicate the approximate timeframe."""

    return prompt


def query_gemini_for_catalysts(tickers: list, scan_date: datetime) -> Optional[dict]:
    """Query Gemini for upcoming catalysts."""
    if not client:
        print("[SCOUT] Gemini client not initialized")
        return None
    
    prompt = get_catalyst_prompt(tickers, scan_date)
    
    # Try each model in the fallback chain
    for model_name in GEMINI_MODELS:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,  # Low temperature for factual responses
                    max_output_tokens=2000,
                )
            )
            
            if response and response.text:
                print(f"  [SCOUT] Using model: {model_name}")
                # Parse JSON from response
                text = response.text.strip()
                
                # Extract JSON if wrapped in markdown code blocks
                if "```json" in text:
                    match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
                    if match:
                        text = match.group(1)
                elif "```" in text:
                    match = re.search(r'```\s*(.*?)\s*```', text, re.DOTALL)
                    if match:
                        text = match.group(1)
                
                try:
                    return json.loads(text)
                except json.JSONDecodeError as e:
                    print(f"  [SCOUT] JSON parse error: {e}")
                    print(f"  [SCOUT] Raw response: {text[:500]}...")
                    return None
                    
        except Exception as e:
            error_str = str(e).lower()
            if "quota" in error_str or "exhausted" in error_str or "429" in error_str:
                print(f"  [SCOUT] Quota exhausted for {model_name}, trying next...")
                continue
            elif "503" in error_str or "unavailable" in error_str:
                print(f"  [SCOUT] Model {model_name} unavailable, trying next...")
                continue
            else:
                print(f"  [SCOUT] Error with {model_name}: {e}")
                continue
    
    print("[SCOUT] All Gemini models exhausted")
    return None


def update_watchlist_priorities(catalysts_data: dict) -> bool:
    """Update the Neon weekly_watchlist table with catalyst info and priorities."""
    if not is_postgres():
        print("[SCOUT] Not using PostgreSQL, skipping database update")
        return False
    
    if not catalysts_data:
        return False
    
    conn = get_connection()
    cur = conn.cursor()
    updated_count = 0
    
    try:
        # Process high-priority catalysts
        for catalyst in catalysts_data.get('catalysts', []):
            ticker = catalyst.get('ticker')
            event = catalyst.get('event', '')
            priority = catalyst.get('priority', 'medium')
            event_date = catalyst.get('date', '')
            
            if ticker:
                catalyst_text = f"{event} ({event_date})" if event_date else event
                cur.execute(
                    """UPDATE weekly_watchlist 
                       SET expected_catalyst = %s, 
                           priority = %s,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE ticker = %s""",
                    (catalyst_text, priority, ticker)
                )
                if cur.rowcount > 0:
                    updated_count += 1
                    print(f"  [SCOUT] Updated {ticker}: {priority} priority - {catalyst_text}")
        
        # Process watchlist status for tickers without high-priority catalysts
        for item in catalysts_data.get('watchlist_status', []):
            ticker = item.get('ticker')
            catalyst = item.get('catalyst', 'No imminent catalysts')
            priority = item.get('priority', 'medium')
            
            if ticker:
                # Only update if not already set to high priority
                cur.execute(
                    """UPDATE weekly_watchlist 
                       SET expected_catalyst = COALESCE(
                           CASE WHEN priority = 'high' THEN expected_catalyst ELSE %s END,
                           %s
                       ),
                           priority = CASE WHEN priority = 'high' THEN priority ELSE %s END,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE ticker = %s AND (priority != 'high' OR priority IS NULL)""",
                    (catalyst, catalyst, priority, ticker)
                )
        
        conn.commit()
        print(f"  [SCOUT] Updated {updated_count} high-priority tickers in database")
        return True
        
    except Exception as e:
        conn.rollback()
        print(f"  [SCOUT] Database update error: {e}")
        return False
    finally:
        conn.close()


def get_current_watchlist_status() -> list:
    """Get the current watchlist status from database for Discord report."""
    if not is_postgres():
        return []
    
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """SELECT ticker, expected_catalyst, priority 
           FROM weekly_watchlist 
           WHERE active = TRUE 
           ORDER BY 
               CASE priority 
                   WHEN 'high' THEN 1 
                   WHEN 'medium' THEN 2 
                   ELSE 3 
               END,
               ticker"""
    )
    rows = cur.fetchall()
    conn.close()
    
    return [
        {
            'ticker': r['ticker'],
            'catalyst': r['expected_catalyst'] or 'No catalyst info',
            'priority': r['priority'] or 'medium'
        }
        for r in rows
    ]


def run_weekly_scout(send_discord: bool = True) -> dict:
    """
    Run the weekly scout to detect upcoming catalysts.
    
    This is designed to run every Monday at 4:00 AM EST.
    
    Args:
        send_discord: Whether to send the Discord report
        
    Returns:
        dict with 'success', 'catalysts', 'watchlist_status' keys
    """
    print("=" * 60)
    print("🔍 WEEKLY SCOUT - Catalyst Detection")
    print("=" * 60)
    
    scan_date = datetime.now(timezone.utc)
    print(f"Scan date: {scan_date.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    
    # Get watchlist tickers
    tickers = get_watchlist_tickers()
    print(f"Watchlist tickers: {', '.join(tickers)}")
    print()
    
    # Query Gemini for catalysts
    print("Querying Gemini for upcoming catalysts...")
    catalysts_data = query_gemini_for_catalysts(tickers, scan_date)
    
    if not catalysts_data:
        print("  [SCOUT] No catalyst data returned from Gemini")
        if send_discord:
            send_system_alert(
                "Weekly Scout Failed",
                "Could not retrieve catalyst data from Gemini. Check API quota."
            )
        return {'success': False, 'catalysts': [], 'watchlist_status': []}
    
    # Extract results
    high_priority = catalysts_data.get('catalysts', [])
    watchlist_status = catalysts_data.get('watchlist_status', [])
    
    print(f"\nFound {len(high_priority)} high-priority catalysts:")
    for cat in high_priority:
        print(f"  🔴 {cat.get('ticker')}: {cat.get('event')} ({cat.get('date')})")
    
    print(f"\nWatchlist status ({len(watchlist_status)} tickers):")
    for item in watchlist_status:
        print(f"  • {item.get('ticker')}: {item.get('catalyst')}")
    
    # Update database
    print("\nUpdating Neon database...")
    db_updated = update_watchlist_priorities(catalysts_data)
    
    # Get final watchlist status from DB for Discord
    final_status = get_current_watchlist_status() if is_postgres() else watchlist_status
    
    # Send Discord report
    if send_discord:
        print("\nSending Discord report...")
        success = send_weekly_scout_report(
            high_priority_catalysts=high_priority,
            watchlist_status=final_status,
            scan_date=scan_date
        )
        if success:
            print("  ✓ Discord report sent")
        else:
            print("  ✗ Discord report failed")
    
    print()
    print("=" * 60)
    print("🏁 WEEKLY SCOUT COMPLETE")
    print("=" * 60)
    
    return {
        'success': True,
        'catalysts': high_priority,
        'watchlist_status': final_status,
        'db_updated': db_updated
    }


if __name__ == "__main__":
    # Run manually for testing
    import argparse
    parser = argparse.ArgumentParser(description="Run weekly scout manually")
    parser.add_argument("--no-discord", action="store_true", help="Skip Discord notification")
    args = parser.parse_args()
    
    result = run_weekly_scout(send_discord=not args.no_discord)
    print(f"\nResult: {json.dumps(result, indent=2, default=str)}")
