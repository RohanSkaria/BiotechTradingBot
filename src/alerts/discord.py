"""
Discord Webhook Notifier

Sends trade alerts, system messages, and weekly scout reports to Discord.
"""

import os
import requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

# New Discord webhook - can be overridden via environment variable
DISCORD_WEBHOOK_URL = os.getenv(
    "DISCORD_WEBHOOK_URL",
    "https://discord.com/api/webhooks/1469773238874931468/Dvzw-vmOR84_1v06PGzN9td844HGeX7fzEvvmD6gmQuYlKQjFuGqxI6IuApEEIQ-X1kf"
)

# Discord user ID for mentions (set in .env as DISCORD_USER_ID)
# To find your ID: Enable Developer Mode in Discord, right-click your name, "Copy ID"
DISCORD_USER_ID = os.getenv("DISCORD_USER_ID")


def get_mention() -> str:
    """Get the mention string for notifications."""
    if DISCORD_USER_ID:
        return f"<@{DISCORD_USER_ID}> "
    return ""  # No mention if user ID not set


def send_message(content: str, username: str = "Biotech Bot", mention: bool = True) -> bool:
    """Send a simple text message to Discord."""
    try:
        # Prepend mention if enabled and user ID is set
        if mention:
            content = get_mention() + content
        
        resp = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": content, "username": username},
            timeout=10,
        )
        return resp.status_code == 204
    except Exception as e:
        print(f"  [DISCORD] Error: {e}")
        return False


def send_trade_alert(
    ticker: str,
    side: str,
    qty: int,
    price: float,
    sentiment: str,
    category: str,
    confidence: int,
    headline: str,
    reasoning: str = "",
) -> bool:
    """Send a formatted trade alert to Discord."""
    emoji = "\U0001f7e2" if side == "buy" else "\U0001f534"  # green/red circle
    direction = "LONG" if side == "buy" else "SHORT"

    content = (
        f"{emoji} **{direction} ${ticker}** | {qty} shares @ ${price:.2f}\n"
        f"> **Category:** {category}\n"
        f"> **Sentiment:** {sentiment} (confidence: {confidence}%)\n"
        f"> **Headline:** {headline[:200]}\n"
    )
    if reasoning:
        content += f"> **AI Reasoning:** {reasoning[:200]}\n"

    content += f"\n`{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}`"

    return send_message(content)


def send_system_alert(title: str, message: str) -> bool:
    """Send a system-level alert (errors, daily summaries, etc.)."""
    content = f"\u2699\ufe0f **[SYSTEM] {title}**\n{message}"
    return send_message(content)


def send_daily_summary(
    trades_today: int,
    pnl: float,
    portfolio_value: float,
    llm_calls: int,
) -> bool:
    """Send end-of-day summary."""
    emoji = "\U0001f4c8" if pnl >= 0 else "\U0001f4c9"  # chart up/down
    pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"

    content = (
        f"{emoji} **Daily Summary**\n"
        f"> Trades: {trades_today}\n"
        f"> P&L: {pnl_str}\n"
        f"> Portfolio: ${portfolio_value:,.2f}\n"
        f"> Gemini API calls: {llm_calls}\n"
        f"\n`{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}`"
    )

    return send_message(content)


def send_weekly_scout_report(
    high_priority_catalysts: list,
    watchlist_status: list,
    scan_date: datetime = None,
) -> bool:
    """
    Send the weekly scout report to Discord.
    
    Args:
        high_priority_catalysts: List of dicts with keys: ticker, event, date, priority
        watchlist_status: List of dicts with keys: ticker, status, catalyst
        scan_date: The date/time of the scan (defaults to now)
    """
    if scan_date is None:
        scan_date = datetime.now(timezone.utc)
    
    # Calculate scan window (last Monday 4AM to this Monday 4AM EST)
    # For display purposes, show the 7-day forward window
    scan_start = scan_date
    scan_end = scan_date + timedelta(days=7)
    
    # Format the report date
    report_date = scan_date.strftime("%a %b %d, %Y")
    
    content = f"\U0001f4c5 **Weekly Scout Report** | {report_date}\n\n"
    
    # High priority catalysts section
    if high_priority_catalysts:
        content += "**HIGH PRIORITY CATALYSTS (Next 7 Days):**\n"
        for cat in high_priority_catalysts:
            emoji = "\U0001f534" if cat.get('priority') == 'high' else "\U0001f7e1"  # red or yellow circle
            ticker = cat.get('ticker', 'N/A')
            event = cat.get('event', 'Unknown event')
            event_date = cat.get('date', 'TBD')
            content += f"{emoji} **{ticker}** - {event} | {event_date}\n"
        content += "\n"
    else:
        content += "**HIGH PRIORITY CATALYSTS (Next 7 Days):**\n"
        content += "_No imminent catalysts detected_\n\n"
    
    # Watchlist status section
    content += "**WATCHLIST STATUS:**\n"
    for item in watchlist_status:
        ticker = item.get('ticker', 'N/A')
        catalyst = item.get('catalyst', 'No catalyst info')
        priority = item.get('priority', 'medium')
        
        if priority == 'high':
            emoji = "\U0001f534"  # red circle
        elif catalyst and catalyst != 'No imminent catalysts':
            emoji = "\u2713"  # checkmark
        else:
            emoji = "\u25cb"  # empty circle
        
        content += f"{emoji} {ticker} - {catalyst}\n"
    
    # Scan window footer
    content += f"\n_Scanned: {scan_start.strftime('%a %b %d %H:%M')} \u2192 {scan_end.strftime('%a %b %d %H:%M')} EST_"
    
    return send_message(content, username="Weekly Scout")


def send_filing_alert(
    ticker: str,
    filing_type: str,
    headline: str,
    filing_url: str,
    sentiment: str = None,
    confidence: int = None,
) -> bool:
    """
    Send an alert when a new SEC filing is detected.
    Called from edgar_8k.py when a filing passes the keyword filter.
    """
    emoji = "\U0001f4e2"  # loudspeaker
    
    content = (
        f"{emoji} **New {filing_type} Filing: ${ticker}**\n"
        f"> {headline[:300]}\n"
    )
    
    if sentiment and confidence:
        sent_emoji = "\U0001f7e2" if "positive" in sentiment.lower() else (
            "\U0001f534" if "negative" in sentiment.lower() else "\U0001f7e1"
        )
        content += f"> **AI Analysis:** {sent_emoji} {sentiment} ({confidence}%)\n"
    
    content += f"> **Filing:** <{filing_url}>\n"
    content += f"\n`{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}`"
    
    return send_message(content)
