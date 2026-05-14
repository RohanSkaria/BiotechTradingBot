"""
Weekly Discovery Module

Uses Gemini with search grounding to discover new biotech tickers with
upcoming catalysts (PDUFA dates, Phase 3 readouts).

Runs Monday 4:00 AM to populate the weekly_watchlist with AI-discovered tickers.
"""

import os
import json
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from db.schema import get_connection, is_postgres
from alerts.discord import send_message

# Gemini model for search grounding
# Note: Search grounding may require specific models
DISCOVERY_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]

# Initialize Gemini client
client = None
try:
    api_key = os.getenv("GEMINI_API_KEY")
    if api_key:
        client = genai.Client(api_key=api_key)
except Exception as e:
    print(f"[DISCOVERY] Warning: Could not initialize Gemini client: {e}")


def get_discovery_prompt(week_start: datetime) -> str:
    """Build the Gemini prompt for ticker discovery."""
    week_end = week_start + timedelta(days=7)
    
    prompt = f"""You are a biotech investment research analyst. Today is {week_start.strftime('%B %d, %Y')}.

Search for biotech and pharmaceutical companies with significant catalysts scheduled for the next 7 days (until {week_end.strftime('%B %d, %Y')}).

Focus on:
1. PDUFA dates (FDA drug approval decisions)
2. Phase 3 clinical trial results/readouts
3. Advisory Committee (AdCom) meetings
4. Complete Response Letter (CRL) resubmission dates
5. Breakthrough Therapy or Fast Track designation decisions

Requirements:
- Companies must have market cap > $500 million
- Include both US and international biotech companies trading on US exchanges
- Only include events with confirmed or expected dates in the next 7 days

Return your findings as a valid JSON array with this exact structure:
{{
    "discoveries": [
        {{
            "ticker": "ACME",
            "company_name": "Acme Biotech Inc",
            "event": "Phase 3 readout for ABC-123 in lung cancer",
            "expected_date": "February 10, 2026",
            "event_type": "phase_3_readout",
            "market_cap_tier": "mid",
            "priority": "high",
            "source": "Company press release / FDA calendar"
        }}
    ],
    "summary": "Found X companies with catalysts this week"
}}

Important:
- Only include real, verifiable catalysts you found in your search
- Do not include companies already in common watchlists (LLY, VRTX, CRSP, REGN, AMGN, GILD)
- If you cannot find any new catalysts, return an empty discoveries array
- Event types: pdufa, phase_3_readout, adcom, crl_response, breakthrough_designation"""

    return prompt


def query_gemini_for_discoveries(week_start: datetime) -> Optional[Dict]:
    """Query Gemini (with search if available) for new ticker discoveries."""
    if not client:
        print("[DISCOVERY] Gemini client not initialized")
        return None
    
    prompt = get_discovery_prompt(week_start)
    
    for model_name in DISCOVERY_MODELS:
        try:
            # Try with search grounding first
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.1,
                        max_output_tokens=2000,
                        # Enable search grounding if available
                        tools=[{"google_search": {}}] if "2.0" in model_name else None
                    )
                )
            except Exception:
                # Fall back without search grounding
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.1,
                        max_output_tokens=4000,
                    )
                )
            
            if response and response.text:
                print(f"  [DISCOVERY] Using model: {model_name}")
                
                text = response.text.strip()
                
                # Extract JSON from response
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
                    print(f"  [DISCOVERY] JSON parse error: {e}")
                    print(f"  [DISCOVERY] Raw: {text[:300]}...")
                    return None
                    
        except Exception as e:
            error_str = str(e).lower()
            if "quota" in error_str or "exhausted" in error_str or "429" in error_str:
                print(f"  [DISCOVERY] Quota exhausted for {model_name}, trying next...")
                continue
            elif "503" in error_str or "unavailable" in error_str:
                print(f"  [DISCOVERY] Model {model_name} unavailable, trying next...")
                continue
            else:
                print(f"  [DISCOVERY] Error with {model_name}: {e}")
                continue
    
    print("[DISCOVERY] All Gemini models exhausted")
    return None


def add_discovered_ticker(
    ticker: str,
    company_name: str,
    event: str,
    expected_date: str,
    event_type: str,
    market_cap_tier: str = "mid",
    priority: str = "medium"
) -> bool:
    """Add a discovered ticker to the weekly_watchlist."""
    if not is_postgres():
        return False
    
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        # Check if ticker already exists
        cur.execute("SELECT ticker FROM weekly_watchlist WHERE ticker = %s", (ticker,))
        if cur.fetchone():
            print(f"    {ticker} already in watchlist, updating...")
            cur.execute("""
                UPDATE weekly_watchlist
                SET expected_catalyst = %s,
                    priority = %s,
                    discovery_source = 'gemini_search',
                    updated_at = CURRENT_TIMESTAMP,
                    active = TRUE
                WHERE ticker = %s
            """, (f"{event} ({expected_date})", priority, ticker))
        else:
            # Insert new ticker
            cur.execute("""
                INSERT INTO weekly_watchlist 
                (ticker, company_name, expected_catalyst, market_cap_tier, priority, 
                 discovery_source, active, cik)
                VALUES (%s, %s, %s, %s, %s, 'gemini_search', TRUE, '')
            """, (ticker, company_name, f"{event} ({expected_date})", market_cap_tier, priority))
        
        conn.commit()
        return True
        
    except Exception as e:
        conn.rollback()
        print(f"    Error adding {ticker}: {e}")
        return False
    finally:
        conn.close()


def expire_old_discoveries(days_old: int = 7) -> int:
    """Mark AI-discovered tickers as inactive if they're older than N days."""
    if not is_postgres():
        return 0
    
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        cur.execute("""
            UPDATE weekly_watchlist
            SET active = FALSE
            WHERE discovery_source = 'gemini_search'
              AND updated_at < CURRENT_TIMESTAMP - INTERVAL '%s days'
              AND active = TRUE
        """, (days_old,))
        expired = cur.rowcount
        conn.commit()
        return expired
    except Exception as e:
        conn.rollback()
        print(f"  Error expiring old discoveries: {e}")
        return 0
    finally:
        conn.close()


def send_discovery_report(discoveries: List[Dict], week_start: datetime) -> bool:
    """Send a Discord report with discovered tickers."""
    if not discoveries:
        content = (
            f"🔍 **Weekly Discovery Report** | {week_start.strftime('%b %d, %Y')}\n\n"
            f"No new biotech catalysts discovered for this week.\n"
            f"_Core watchlist remains active._"
        )
    else:
        content = (
            f"🔍 **Weekly Discovery Report** | {week_start.strftime('%b %d, %Y')}\n\n"
            f"**{len(discoveries)} New Catalyst(s) Found:**\n"
        )
        
        for d in discoveries[:10]:  # Limit to 10 in Discord
            emoji = "🔴" if d.get('priority') == 'high' else "🟡"
            content += (
                f"{emoji} **${d.get('ticker')}** - {d.get('company_name', 'Unknown')}\n"
                f"> {d.get('event', 'Unknown event')}\n"
                f"> Date: {d.get('expected_date', 'TBD')} | Type: {d.get('event_type', 'unknown')}\n\n"
            )
        
        content += f"_These tickers have been added to the watchlist for 7 days._"
    
    return send_message(content, username="Weekly Discovery")


def run_weekly_discovery(send_discord: bool = True) -> Dict:
    """
    Main function: Run the weekly ticker discovery.
    
    This is designed to run every Monday at 4:00 AM EST, after the weekly scout.
    """
    print("=" * 60)
    print("🔍 WEEKLY DISCOVERY - AI Ticker Search")
    print("=" * 60)
    
    week_start = datetime.now(timezone.utc)
    print(f"Week of: {week_start.strftime('%Y-%m-%d')}")
    print()
    
    # Expire old AI-discovered tickers
    print("Expiring old discoveries...")
    expired = expire_old_discoveries(days_old=7)
    print(f"  Expired {expired} old discoveries")
    print()
    
    # Query Gemini for new discoveries
    print("Querying Gemini for new biotech catalysts...")
    discovery_data = query_gemini_for_discoveries(week_start)
    
    results = {
        'success': False,
        'discoveries': [],
        'added': 0,
        'expired': expired
    }
    
    if not discovery_data:
        print("  No discovery data returned")
        if send_discord:
            send_discovery_report([], week_start)
        return results
    
    discoveries = discovery_data.get('discoveries', [])
    summary = discovery_data.get('summary', '')
    
    print(f"\n{summary}")
    print(f"Found {len(discoveries)} potential ticker(s)")
    print()
    
    # Add discovered tickers to watchlist
    for d in discoveries:
        ticker = d.get('ticker', '').upper()
        if not ticker:
            continue
        
        print(f"  Adding {ticker}...", end=" ")
        
        if add_discovered_ticker(
            ticker=ticker,
            company_name=d.get('company_name', ''),
            event=d.get('event', 'AI-discovered catalyst'),
            expected_date=d.get('expected_date', 'This week'),
            event_type=d.get('event_type', 'unknown'),
            market_cap_tier=d.get('market_cap_tier', 'mid'),
            priority=d.get('priority', 'medium')
        ):
            results['added'] += 1
            results['discoveries'].append(d)
            print("✓")
        else:
            print("✗")
    
    results['success'] = True
    
    # Send Discord report
    if send_discord:
        print("\nSending Discord report...")
        send_discovery_report(results['discoveries'], week_start)
    
    print()
    print("=" * 60)
    print(f"🏁 WEEKLY DISCOVERY COMPLETE")
    print(f"   Added: {results['added']} | Expired: {results['expired']}")
    print("=" * 60)
    
    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Weekly Ticker Discovery")
    parser.add_argument("--no-discord", action="store_true", help="Skip Discord notification")
    args = parser.parse_args()
    
    result = run_weekly_discovery(send_discord=not args.no_discord)
    print(f"\nResult: {json.dumps(result, indent=2, default=str)}")
