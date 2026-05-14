#!/usr/bin/env python3
"""
Stress Test: Synthetic 8-K Event
================================
Injects a fake "Strong Positive" Phase 3 trial success 8-K for LLY
and runs it through the entire pipeline to verify order execution.
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv()

from db.schema import init_db
from db.storage import insert_news_event, insert_classification
from analysis.keyword_filter import filter_headline
from analysis.gemini_classifier import classify_headline
from trading.risk_manager import calculate_position_size, get_stop_loss_pct
from trading.executor import submit_order, get_account
from alerts.discord import send_message as send_discord_alert


# ============================================================================
# SYNTHETIC 8-K DATA - Fake Phase 3 Trial Success for LLY
# ============================================================================

SYNTHETIC_8K = {
    "ticker": "LLY",
    "headline": "Item 8.01 - Eli Lilly Announces Positive Phase 3 SURPASS-5 Results: Tirzepatide Achieves Primary Endpoint with Statistically Significant A1C Reduction and Superior Weight Loss in Patients with Type 2 Diabetes",
    "source": "SEC-EDGAR-8K",
    "raw_text": """
UNITED STATES SECURITIES AND EXCHANGE COMMISSION
Washington, D.C. 20549
FORM 8-K
CURRENT REPORT

Pursuant to Section 13 or 15(d) of the Securities Exchange Act of 1934

ELI LILLY AND COMPANY
(Exact name of registrant as specified in its charter)

Item 8.01 Other Events

PHASE 3 SURPASS-5 TRIAL RESULTS - POSITIVE TOPLINE DATA

Eli Lilly and Company (NYSE: LLY) today announced positive topline results from 
the pivotal Phase 3 SURPASS-5 clinical trial evaluating tirzepatide in adults 
with type 2 diabetes inadequately controlled with basal insulin.

KEY FINDINGS:
- Primary endpoint MET: Tirzepatide demonstrated statistically significant 
  superior A1C reduction compared to placebo (p<0.001)
- All doses (5mg, 10mg, 15mg) achieved the primary endpoint
- Mean A1C reduction of 2.4% from baseline at 40 weeks
- 97% of patients on highest dose achieved A1C <7%
- Superior weight loss: Mean body weight reduction of 15.7% (approximately 35 lbs)
- Safety profile consistent with prior SURPASS trials
- No new safety signals identified

"These results reinforce tirzepatide's potential as a transformative therapy 
for patients with type 2 diabetes," said Dr. John Smith, Senior Vice President 
of Lilly Diabetes. "We look forward to discussing these data with regulatory 
authorities."

The Company plans to submit these data to the FDA as part of the ongoing 
regulatory review process. A complete data presentation is scheduled for the 
upcoming American Diabetes Association Scientific Sessions.

FORWARD-LOOKING STATEMENTS
This press release contains forward-looking statements...
""",
    "published": datetime.now(timezone.utc).isoformat(),
    "filing_url": "https://www.sec.gov/synthetic-stress-test"
}


def run_stress_test():
    """Run the full pipeline with synthetic data."""
    
    print("=" * 70)
    print("🧪 STRESS TEST: Synthetic Phase 3 8-K Event")
    print("=" * 70)
    print()
    
    # Step 1: Initialize DB
    print("📦 Step 1: Initializing database...")
    init_db()
    print("   ✓ Database ready")
    print()
    
    # Step 2: Display synthetic event
    print("📰 Step 2: Synthetic 8-K Event Details")
    print("-" * 50)
    print(f"   Ticker:   {SYNTHETIC_8K['ticker']}")
    print(f"   Source:   {SYNTHETIC_8K['source']}")
    print(f"   Headline: {SYNTHETIC_8K['headline'][:80]}...")
    print(f"   Text:     {len(SYNTHETIC_8K['raw_text'])} characters")
    print()
    
    # Step 3: Keyword Filter (Tier 1)
    print("🔍 Step 3: Tier 1 - Keyword Filter")
    print("-" * 50)
    
    # Combine headline + raw_text for filtering
    combined_text = f"{SYNTHETIC_8K['headline']} {SYNTHETIC_8K['raw_text']}"
    filter_result = filter_headline(combined_text)
    
    print(f"   Is Relevant: {filter_result.is_relevant}")
    print(f"   Score:       {filter_result.score}")
    print(f"   Direction:   {filter_result.direction}")
    print(f"   Keywords:    {filter_result.matched_keywords}")
    print()
    
    if not filter_result.is_relevant:
        print("❌ Event did not pass keyword filter. Proceeding anyway for stress test...")
        # For stress test, we proceed anyway
    
    # Step 4: Insert into DB
    print("💾 Step 4: Inserting event into database...")
    event_id = insert_news_event(
        ticker=SYNTHETIC_8K['ticker'],
        headline=SYNTHETIC_8K['headline'],
        filing_type="8-K",
        accession_number="STRESS-TEST-001",
        filing_url=SYNTHETIC_8K['filing_url'],
        raw_text=SYNTHETIC_8K['raw_text'],
        timestamp=SYNTHETIC_8K['published'],
        source=SYNTHETIC_8K['source']
    )
    print(f"   ✓ Event ID: {event_id}")
    print()
    
    # Step 5: LLM Classification (Tier 2)
    print("🤖 Step 5: Tier 2 - Gemini LLM Classification")
    print("-" * 50)
    
    classification = classify_headline(
        headline=SYNTHETIC_8K['headline'],
        extra_context=f"Ticker: {SYNTHETIC_8K['ticker']}\n\n{SYNTHETIC_8K['raw_text']}"
    )
    
    if classification:
        print(f"   Category:   {classification.get('category', 'N/A')}")
        print(f"   Sentiment:  {classification.get('sentiment', 'N/A')}")
        print(f"   Confidence: {classification.get('confidence', 'N/A')}")
        print(f"   Tickers:    {classification.get('tickers', [])}")
        print(f"   Rationale:  {classification.get('rationale', 'N/A')[:100]}...")
        
        # Store classification
        affected = classification.get('tickers', [SYNTHETIC_8K['ticker']])
        insert_classification(
            news_id=event_id if event_id else 0,
            category=classification.get('category', 'unknown'),
            sentiment=classification.get('sentiment', 'neutral'),
            confidence=int(classification.get('confidence', 50)),
            primary_ticker=SYNTHETIC_8K['ticker'],
            affected_tickers=affected if affected else [SYNTHETIC_8K['ticker']],
            reasoning=classification.get('rationale', ''),
            token_count=classification.get('token_count', 0)
        )
        print("   ✓ Classification stored")
    else:
        print("   ⚠️  Classification failed, using fallback...")
        classification = {
            'category': 'clinical_trial',
            'sentiment': 'strong_positive',
            'confidence': 95,
            'tickers': ['LLY'],
            'rationale': 'Phase 3 trial success - stress test fallback'
        }
        insert_classification(
            news_id=event_id if event_id else 0,
            category=classification['category'],
            sentiment=classification['sentiment'],
            confidence=classification['confidence'],
            primary_ticker=SYNTHETIC_8K['ticker'],
            affected_tickers=classification['tickers'],
            reasoning=classification['rationale'],
            token_count=0
        )
    print()
    
    # Step 6: Check if signal is tradeable
    print("📊 Step 6: Signal Evaluation")
    print("-" * 50)
    
    sentiment = classification.get('sentiment', 'neutral')
    confidence = classification.get('confidence', 0)
    
    # Normalize sentiment for comparison (handle Gemini's "Strong Positive" format)
    sentiment_lower = sentiment.lower().replace(" ", "_") if sentiment else "neutral"
    
    is_strong_positive = sentiment_lower in ['strong_positive', 'positive'] and confidence >= 70
    is_strong_negative = sentiment_lower in ['strong_negative', 'negative'] and confidence >= 70
    
    print(f"   Sentiment:        {sentiment}")
    print(f"   Sentiment (norm): {sentiment_lower}")
    print(f"   Confidence:       {confidence}")
    print(f"   Strong Positive:  {is_strong_positive}")
    print(f"   Strong Negative:  {is_strong_negative}")
    
    if not (is_strong_positive or is_strong_negative):
        print()
        print("⚠️  Signal not strong enough for trade. Forcing trade for stress test...")
        is_strong_positive = True
    print()
    
    # Step 7: Risk Management
    print("⚖️  Step 7: Risk Management")
    print("-" * 50)
    
    ticker = SYNTHETIC_8K['ticker']
    
    # Get current account
    account = get_account()
    if account:
        equity = float(account.get('equity', 100000))
        print(f"   Account Equity: ${equity:,.2f}")
    else:
        equity = 100000
        print(f"   Account Equity: ${equity:,.2f} (simulated)")
    
    # Fetch current price for LLY
    import yfinance as yf
    stock = yf.Ticker(ticker)
    try:
        current_price = stock.info.get('currentPrice') or stock.info.get('regularMarketPrice') or stock.fast_info.get('lastPrice')
    except Exception:
        current_price = None
    
    if not current_price:
        # Fallback: try historical data
        hist = stock.history(period="1d")
        if not hist.empty:
            current_price = hist['Close'].iloc[-1]
        else:
            current_price = 900.0  # Hardcoded fallback for LLY
            print(f"   ⚠️  Could not fetch price, using ${current_price}")
    
    print(f"   Current Price:    ${current_price:.2f}")
    
    # Calculate position - use the Gemini sentiment format directly
    position_info = calculate_position_size(
        ticker=ticker,
        price=current_price,
        sentiment=sentiment,  # Keep original format "Strong Positive"
        confidence=int(confidence)
    )
    
    print(f"   Position Allowed: {position_info['allowed']}")
    print(f"   Reason:           {position_info['reason']}")
    if position_info['allowed']:
        print(f"   Position Size:    ${position_info['dollar_amount']:,.2f}")
        print(f"   Shares:           {position_info['qty']}")
        print(f"   Stop Loss:        ${position_info['stop_loss_price']:.2f} ({position_info['stop_loss_pct']*100:.1f}%)")
        print(f"   Take Profit:      ${position_info['take_profit_price']:.2f} ({position_info['take_profit_pct']*100:.1f}%)")
    print()
    
    # Step 8: Execute Trade
    print("🚀 Step 8: Trade Execution")
    print("-" * 50)
    
    if not position_info['allowed'] or position_info['qty'] <= 0:
        print("   ❌ Cannot place order: Position not allowed or 0 shares")
        print(f"      Reason: {position_info['reason']}")
        print()
        print("=" * 70)
        print("🏁 STRESS TEST COMPLETE (No order placed)")
        print("=" * 70)
        return
    
    side = "buy" if is_strong_positive else "sell"
    print(f"   Side:             {side.upper()}")
    print(f"   Ticker:           {ticker}")
    print(f"   Quantity:         {position_info['qty']}")
    print(f"   Time-in-Force:    IOC (Immediate or Cancel)")
    print()
    
    # Confirm before placing
    print("   ⚠️  PLACING REAL PAPER TRADE ORDER...")
    print()
    
    order_result = submit_order(
        ticker=ticker,
        qty=position_info['qty'],
        side=side,
        order_type="market",
        time_in_force="ioc"  # Immediate or Cancel for safety
    )
    
    if order_result:
        print("   ✅ ORDER PLACED SUCCESSFULLY!")
        print(f"      Order ID:     {order_result.get('id', 'N/A')}")
        print(f"      Status:       {order_result.get('status', 'N/A')}")
        print(f"      Symbol:       {order_result.get('symbol', 'N/A')}")
        print(f"      Qty:          {order_result.get('qty', 'N/A')}")
        print(f"      Side:         {order_result.get('side', 'N/A')}")
        print(f"      Type:         {order_result.get('type', 'N/A')}")
        print(f"      TIF:          {order_result.get('time_in_force', 'N/A')}")
        print(f"      Submitted:    {order_result.get('submitted_at', 'N/A')}")
        
        # Send Discord alert
        print()
        print("📢 Sending Discord Alert...")
        alert_msg = f"""🧪 **STRESS TEST ORDER PLACED**
        
**Ticker:** {ticker}
**Side:** {side.upper()}
**Shares:** {position_info['qty']}
**Order ID:** {order_result.get('id', 'N/A')}
**Status:** {order_result.get('status', 'N/A')}

**Signal:**
- Category: {classification.get('category')}
- Sentiment: {classification.get('sentiment')}
- Confidence: {classification.get('confidence')}

**Risk Parameters:**
- Entry: ${current_price:.2f}
- Stop Loss: ${position_info['stop_loss_price']:.2f}
- Take Profit: ${position_info['take_profit_price']:.2f}

_This was a synthetic stress test._"""
        
        send_discord_alert(alert_msg)
        print("   ✓ Discord alert sent")
        
    else:
        print("   ❌ ORDER FAILED!")
        print("      Check Alpaca API credentials and account status")
    
    print()
    print("=" * 70)
    print("🏁 STRESS TEST COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    run_stress_test()
