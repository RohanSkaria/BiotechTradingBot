#!/usr/bin/env python3
"""
Integration Test - Heartbeat Check

Tests connectivity to all three critical APIs:
1. Neon PostgreSQL (DATABASE_URL)
2. Gemini AI (GEMINI_API_KEY)
3. Alpaca Paper Trading (ALPACA_KEY, ALPACA_SECRET)

Run this before deploying to VM to ensure all services are connected.
"""

import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()


def test_neon() -> bool:
    """Test Neon PostgreSQL connection."""
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        
        url = os.getenv("DATABASE_URL")
        if not url:
            print("❌ Neon Postgres: DATABASE_URL not set")
            return False
        
        conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
        cur = conn.cursor()
        
        # Test query
        cur.execute("SELECT COUNT(*) as cnt FROM weekly_watchlist WHERE active = TRUE")
        result = cur.fetchone()
        count = result['cnt']
        
        # Get tickers
        cur.execute("SELECT ticker FROM weekly_watchlist WHERE active = TRUE")
        tickers = [r['ticker'] for r in cur.fetchall()]
        
        conn.close()
        print(f"✅ Neon Postgres: Connected ({count} active tickers: {', '.join(tickers)})")
        return True
        
    except Exception as e:
        print(f"❌ Neon Postgres: {e}")
        return False


def test_gemini() -> bool:
    """Test Gemini AI connection."""
    try:
        from google import genai
        from google.genai import types
        
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            print("❌ Gemini AI: GEMINI_API_KEY not set")
            return False
        
        client = genai.Client(api_key=api_key)
        
        # Try models in fallback order
        models = ["gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-2.5-flash-lite", "gemini-2.5-flash"]
        
        for model_name in models:
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents="Reply with only the word 'OK' to confirm connection.",
                    config=types.GenerateContentConfig(max_output_tokens=10)
                )
                if response and response.text:
                    print(f"✅ Gemini AI: Connected (model: {model_name})")
                    return True
            except Exception as e:
                if "quota" in str(e).lower() or "429" in str(e):
                    continue
                elif "503" in str(e) or "unavailable" in str(e).lower():
                    continue
        
        print("❌ Gemini AI: All models quota exhausted or unavailable")
        return False
        
    except Exception as e:
        print(f"❌ Gemini AI: {e}")
        return False


def test_alpaca() -> bool:
    """Test Alpaca Paper Trading connection."""
    try:
        import requests
        
        api_key = os.getenv("ALPACA_KEY")
        api_secret = os.getenv("ALPACA_SECRET")
        
        if not api_key or not api_secret:
            print("❌ Alpaca Paper: ALPACA_KEY or ALPACA_SECRET not set")
            return False
        
        url = "https://paper-api.alpaca.markets/v2/account"
        headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": api_secret
        }
        
        resp = requests.get(url, headers=headers, timeout=10)
        
        if resp.status_code == 200:
            data = resp.json()
            status = data.get('status', 'unknown')
            equity = float(data.get('equity', 0))
            print(f"✅ Alpaca Paper: {status} (equity: ${equity:,.2f})")
            return True
        else:
            print(f"❌ Alpaca Paper: HTTP {resp.status_code} - {resp.text[:100]}")
            return False
            
    except Exception as e:
        print(f"❌ Alpaca Paper: {e}")
        return False


def test_discord() -> bool:
    """Test Discord webhook connection."""
    try:
        import requests

        webhook_url = (os.getenv("DISCORD_WEBHOOK_URL") or "").strip()
        if not webhook_url:
            print("❌ Discord Webhook: DISCORD_WEBHOOK_URL not set in .env")
            return False

        payload = {
            "content": f"🔔 **Heartbeat Test** - Integration check at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            "username": "Heartbeat Bot"
        }
        
        resp = requests.post(webhook_url, json=payload, timeout=10)
        
        if resp.status_code == 204:
            print("✅ Discord Webhook: Connected (test message sent)")
            return True
        else:
            print(f"❌ Discord Webhook: HTTP {resp.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Discord Webhook: {e}")
        return False


def heartbeat(include_discord: bool = True) -> bool:
    """
    Run all integration tests.
    
    Returns True if all critical services pass.
    """
    print("=" * 50)
    print("🫀 HEARTBEAT - Integration Test")
    print(f"   {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 50)
    print()
    
    results = {
        'neon': test_neon(),
        'gemini': test_gemini(),
        'alpaca': test_alpaca(),
    }
    
    if include_discord:
        results['discord'] = test_discord()
    
    print()
    print("-" * 50)
    
    passed = sum(results.values())
    total = len(results)
    
    if all(results.values()):
        print(f"✅ ALL SYSTEMS GO ({passed}/{total} passed)")
        print("   Your bot is ready for deployment!")
        return True
    else:
        failed = [k for k, v in results.items() if not v]
        print(f"⚠️  SOME SYSTEMS FAILED ({passed}/{total} passed)")
        print(f"   Failed: {', '.join(failed)}")
        return False


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run integration heartbeat test")
    parser.add_argument("--no-discord", action="store_true", help="Skip Discord test")
    args = parser.parse_args()
    
    success = heartbeat(include_discord=not args.no_discord)
    sys.exit(0 if success else 1)
