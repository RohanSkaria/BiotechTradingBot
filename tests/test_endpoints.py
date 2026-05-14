"""
Phase 0: Endpoint Verification Tests
Verifies JSON formatting and connectivity for all 4 external services
before any pipeline code is built.

Run: python tests/test_endpoints.py
"""

import os
import sys
import json
import time
import requests
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

DISCORD_WEBHOOK_URL = (
    "https://discord.com/api/webhooks/1469364077804388392/"
    "yScDdd1nq_UiGBXLXEPlYGBOubpxg53EOIiSw_uBBcW0bRlU54hyOQGc-JP_fmruDEzS"
)

SEC_HEADERS = {
    'User-Agent': 'Rohan Skaria (rohan.skaria@email.com)',
    'Accept-Encoding': 'gzip, deflate',
    'Host': 'data.sec.gov'
}

# Eli Lilly CIK for testing
TEST_CIK = "0000059478"


def test_discord_webhook():
    """POST a test message to the Discord webhook and verify 204 response."""
    print("\n" + "=" * 60)
    print("TEST 1: Discord Webhook")
    print("=" * 60)

    payload = {
        "content": "**[BIOTECH BOT]** Endpoint test -- if you see this, Discord webhook is working.",
        "username": "Biotech Bot Test"
    }

    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        print(f"  Status Code: {resp.status_code}")
        print(f"  Expected:    204 (No Content)")

        if resp.status_code == 204:
            print("  Result:      PASS")
            return True
        else:
            print(f"  Result:      FAIL -- got {resp.status_code}")
            print(f"  Response:    {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"  Result:      FAIL -- {e}")
        return False


def test_alpaca_account():
    """GET /v2/account from Alpaca paper trading and verify JSON response."""
    print("\n" + "=" * 60)
    print("TEST 2: Alpaca Paper Trading API")
    print("=" * 60)

    api_key = os.getenv("ALPACA_KEY")
    api_secret = os.getenv("ALPACA_SECRET")

    if not api_key or not api_secret:
        print("  Result:      FAIL -- ALPACA_KEY or ALPACA_SECRET not set in .env")
        return False

    print(f"  ALPACA_KEY:  {api_key[:4]}...{api_key[-4:]}")

    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": api_secret,
    }

    try:
        resp = requests.get(
            "https://paper-api.alpaca.markets/v2/account",
            headers=headers,
            timeout=10
        )
        print(f"  Status Code: {resp.status_code}")
        print(f"  Expected:    200")

        if resp.status_code == 200:
            data = resp.json()
            print(f"  Account #:   {data.get('account_number', 'N/A')}")
            print(f"  Status:      {data.get('status', 'N/A')}")
            print(f"  Portfolio $: {data.get('portfolio_value', 'N/A')}")
            print(f"  Buying Power: {data.get('buying_power', 'N/A')}")
            print(f"  Currency:    {data.get('currency', 'N/A')}")

            # Verify key fields exist
            required_fields = ['account_number', 'status', 'portfolio_value']
            missing = [f for f in required_fields if f not in data]
            if missing:
                print(f"  Result:      FAIL -- missing fields: {missing}")
                return False

            print("  Result:      PASS")
            return True
        else:
            print(f"  Result:      FAIL -- got {resp.status_code}")
            print(f"  Response:    {resp.text[:300]}")
            return False
    except Exception as e:
        print(f"  Result:      FAIL -- {e}")
        return False


def test_sec_edgar():
    """GET Eli Lilly's filing history from SEC EDGAR and verify 8-K entries exist."""
    print("\n" + "=" * 60)
    print("TEST 3: SEC EDGAR (Eli Lilly - CIK 0000059478)")
    print("=" * 60)

    url = f"https://data.sec.gov/submissions/CIK{TEST_CIK}.json"

    try:
        resp = requests.get(url, headers=SEC_HEADERS, timeout=15)
        print(f"  Status Code: {resp.status_code}")
        print(f"  Expected:    200")

        if resp.status_code == 200:
            data = resp.json()
            company_name = data.get("name", "N/A")
            print(f"  Company:     {company_name}")

            # Navigate to recent filings
            recent = data.get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            dates = recent.get("filingDate", [])
            descriptions = recent.get("primaryDocDescription", [])
            accession_numbers = recent.get("accessionNumber", [])

            print(f"  Total Recent Filings: {len(forms)}")

            # Find 8-K filings specifically
            eight_k_indices = [i for i, f in enumerate(forms) if f == "8-K"]
            print(f"  8-K Filings Found:    {len(eight_k_indices)}")

            if eight_k_indices:
                # Show the 3 most recent 8-Ks
                print(f"\n  Most recent 8-K filings:")
                for idx in eight_k_indices[:3]:
                    print(f"    - Date: {dates[idx]}, "
                          f"Accession: {accession_numbers[idx]}, "
                          f"Desc: {descriptions[idx] if idx < len(descriptions) else 'N/A'}")

                print("\n  Result:      PASS")
                return True
            else:
                print("  Result:      FAIL -- no 8-K filings found in recent filings")
                return False
        else:
            print(f"  Result:      FAIL -- got {resp.status_code}")
            print(f"  Response:    {resp.text[:300]}")
            return False
    except Exception as e:
        print(f"  Result:      FAIL -- {e}")
        return False


def test_gemini():
    """Send a biotech classification prompt to Gemini and verify structured JSON response."""
    print("\n" + "=" * 60)
    print("TEST 4: Gemini API (google-genai)")
    print("=" * 60)

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("  Result:      FAIL -- GEMINI_API_KEY not set in .env")
        return False

    print(f"  API Key:     {api_key[:4]}...{api_key[-4:]}")

    try:
        from google import genai

        client = genai.Client(api_key=api_key)

        # Simulate a real biotech classification task
        test_headline = (
            "Eli Lilly announces Phase 3 trial of oral Zepbound meets "
            "primary endpoint with statistically significant weight loss"
        )

        prompt = f"""You are a biotech news classifier. Analyze this headline and respond ONLY with valid JSON (no markdown fences).

Headline: "{test_headline}"

Respond with this exact JSON structure:
{{
    "category": "<one of: Clinical Trial Result, FDA Decision, Competitive Threat, Offering/Dilution, M&A, Earnings, Partnership>",
    "sentiment": "<one of: Strong Positive, Weak Positive, Neutral, Weak Negative, Strong Negative>",
    "confidence": <0-100 integer>,
    "primary_ticker": "<main ticker affected>",
    "affected_tickers": ["<list of all tickers affected including competitors in same therapeutic area>"],
    "reasoning": "<one sentence explanation>"
}}"""

        # Try models in order of preference -- free tier quotas are per-model
        models_to_try = [
            "gemini-2.0-flash",
            "gemini-2.0-flash-lite",
            "gemini-2.5-flash-lite",
            "gemini-2.5-flash",
        ]
        response = None
        used_model = None

        for model_name in models_to_try:
            try:
                print(f"  Trying model: {model_name}...")
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config={
                        "max_output_tokens": 200,
                        "temperature": 0.1,
                    }
                )
                used_model = model_name
                break
            except Exception as model_err:
                err_str = str(model_err)
                if "429" in err_str or "quota" in err_str.lower():
                    print(f"    -> Quota exhausted for {model_name}, trying next...")
                    continue
                else:
                    raise model_err

        if response is None:
            print("  Result:      FAIL -- all model quotas exhausted")
            print("  Tip:         Wait for quota reset or enable billing at https://ai.google.dev")
            return False

        print(f"  Model used:  {used_model}")
        raw_text = response.text.strip()
        print(f"  Raw response length: {len(raw_text)} chars")

        # Try to parse JSON (strip markdown code fences if present)
        json_text = raw_text
        if json_text.startswith("```"):
            lines = json_text.split("\n")
            json_text = "\n".join(lines[1:-1])

        parsed = json.loads(json_text)
        print(f"  Parsed JSON: YES")
        print(f"  Category:    {parsed.get('category', 'N/A')}")
        print(f"  Sentiment:   {parsed.get('sentiment', 'N/A')}")
        print(f"  Confidence:  {parsed.get('confidence', 'N/A')}")
        print(f"  Ticker:      {parsed.get('primary_ticker', 'N/A')}")
        print(f"  Affected:    {parsed.get('affected_tickers', 'N/A')}")
        print(f"  Reasoning:   {parsed.get('reasoning', 'N/A')}")

        # Verify required fields
        required = ['category', 'sentiment', 'confidence', 'primary_ticker', 'affected_tickers']
        missing = [f for f in required if f not in parsed]
        if missing:
            print(f"  Result:      FAIL -- missing fields: {missing}")
            return False

        print("  Result:      PASS")
        return True

    except json.JSONDecodeError as e:
        print(f"  JSON Parse:  FAIL -- {e}")
        print(f"  Raw text:    {raw_text[:300]}")
        return False
    except Exception as e:
        print(f"  Result:      FAIL -- {e}")
        return False


def main():
    print("=" * 60)
    print("BIOTECH TRADING BOT -- ENDPOINT VERIFICATION")
    print("=" * 60)
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    results = {}
    results["Discord Webhook"] = test_discord_webhook()
    results["Alpaca Paper API"] = test_alpaca_account()
    results["SEC EDGAR"] = test_sec_edgar()
    results["Gemini API"] = test_gemini()

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  {name:25s} {status}")

    total = len(results)
    passed = sum(1 for v in results.values() if v)
    print(f"\n  {passed}/{total} endpoints verified successfully.")

    if passed < total:
        print("\n  Fix failing endpoints before proceeding to Phase 1.")
        sys.exit(1)
    else:
        print("\n  All endpoints OK. Ready for Phase 1: Data Pipeline.")
        sys.exit(0)


if __name__ == "__main__":
    main()
