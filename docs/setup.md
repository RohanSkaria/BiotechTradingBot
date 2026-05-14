# Setup Guide

Complete installation and configuration instructions for the Biotech Trading Bot.

## Prerequisites

- Python 3.10 or higher
- Internet connection (for API calls)
- Accounts for:
  - [Alpaca Paper Trading](https://alpaca.markets) (free)
  - [Google AI Studio](https://aistudio.google.com) (free tier available)
  - Discord (for webhook notifications, optional)

## Step 1: Clone and Install

```bash
# Clone the repository (or navigate to your project directory)
cd BIOTECH-TRADING-BOT

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

## Step 2: Get API Keys

### Alpaca Paper Trading

1. Sign up at [alpaca.markets](https://alpaca.markets)
2. Create a paper trading account (free)
3. Navigate to **Dashboard → API Keys**
4. Copy your **API Key ID** and **Secret Key**

### Google Gemini API

1. Go to [Google AI Studio](https://aistudio.google.com)
2. Sign in with your Google account
3. Click **Get API Key** → Create API Key
4. Copy the API key (starts with `AIza...`)

**Note**: The free tier provides 60 requests/minute for Gemini Flash models, which is sufficient for paper trading.

### Discord Webhook (Optional)

1. Open Discord and create a server (or use an existing one)
2. Go to **Server Settings → Integrations → Webhooks**
3. Click **New Webhook**
4. Name it "Biotech Bot" and copy the **Webhook URL**

The webhook URL is already configured in the code. If you want to use a different one, edit `src/alerts/discord.py`.

## Step 3: Configure Environment Variables

Create a `.env` file in the project root:

```bash
# .env file
ALPACA_KEY=your_alpaca_key_here
ALPACA_SECRET=your_alpaca_secret_here
GEMINI_API_KEY=your_gemini_key_here
```

**Important**: Never commit `.env` to version control. It's already in `.gitignore`.

## Step 4: Verify Endpoints

Run the endpoint verification test to ensure all services are accessible:

```bash
python tests/test_endpoints.py
```

Expected output:
```
============================================================
BIOTECH TRADING BOT -- ENDPOINT VERIFICATION
============================================================
TEST 1: Discord Webhook
  Status Code: 204
  Result:      PASS

TEST 2: Alpaca Paper Trading API
  Account #:   PA361PG34M78
  Status:      ACTIVE
  Portfolio $: 100000
  Result:      PASS

TEST 3: SEC EDGAR
  Company:     ELI LILLY & Co
  8-K Filings Found:    49
  Result:      PASS

TEST 4: Gemini API
  Model used:  gemini-2.5-flash-lite
  Category:    Clinical Trial Result
  Sentiment:   Strong Positive
  Result:      PASS

SUMMARY
  4/4 endpoints verified successfully.
```

If any test fails, see [Troubleshooting](troubleshooting.md).

## Step 5: Initialize Database

The database is auto-created on first run, but you can initialize it manually:

```bash
python -c "from src.db.schema import init_db; init_db()"
```

This creates `data/biotech_bot.db` with all required tables.

## Step 6: Configure Watchlist

Edit `config/watchlist.yaml` to add or remove tickers:

```yaml
tickers:
  LLY:
    name: "Eli Lilly"
    cik: "0000059478"
    therapeutic_area: "GLP-1 / Obesity"
    key_catalyst: "Oral Zepbound approval"
    market_cap_tier: "large"
```

To find a company's CIK number:
- Visit [SEC EDGAR Company Search](https://www.sec.gov/edgar/searchedgar/companysearch.html)
- Search by ticker symbol
- The CIK is shown in the results (format: `0000059478`)

## Step 7: Test Run

Run a single pipeline cycle to verify everything works:

```bash
python src/main.py --once --lookback 7
```

This will:
1. Poll SEC EDGAR for filings from the last 7 days
2. Filter headlines through keyword filter
3. Classify relevant ones with Gemini
4. Calculate position sizes (but won't execute trades unless you remove the safety check)

## Step 8: Start the Bot

For continuous operation:

```bash
python src/main.py
```

The bot will:
- Poll EDGAR every 60 seconds (configurable via `--interval`)
- Process new 8-K filings
- Execute trades when signals meet criteria
- Send Discord alerts for each trade

Press `Ctrl+C` to stop.

## Configuration Options

### Command-Line Arguments

```bash
python src/main.py --help
```

Options:
- `--once`: Run a single cycle and exit (useful for testing)
- `--interval N`: Poll interval in seconds (default: 60)
- `--lookback N`: Days to look back for EDGAR filings (default: 1)

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `ALPACA_KEY` | Alpaca API key ID | Yes |
| `ALPACA_SECRET` | Alpaca API secret key | Yes |
| `GEMINI_API_KEY` | Google Gemini API key | Yes |

### Risk Parameters

Edit `src/trading/risk_manager.py` to adjust:

- `MAX_POSITION_PCT`: Max % of portfolio per trade (default: 0.05 = 5%)
- `MAX_DAILY_LOSS_PCT`: Stop trading if daily loss exceeds this (default: 0.02 = 2%)
- `DEFAULT_STOP_LOSS_PCT`: Stop loss per trade (default: 0.05 = 5%)
- `DEFAULT_TAKE_PROFIT_PCT`: Take profit per trade (default: 0.10 = 10%)
- `MAX_OPEN_POSITIONS`: Max concurrent positions (default: 5)

### LLM Cost Controls

Edit `src/analysis/cost_guard.py`:

- `DAILY_CALL_LIMIT`: Max Gemini calls per day (default: 100)
- `MODEL_FALLBACK_CHAIN`: Models to try in order (default: 2.0-flash → 2.5-flash-lite → 2.5-flash)

## Next Steps

- Read [Usage Guide](usage.md) to understand how to run and monitor the bot
- Review [Architecture](architecture.md) to understand the system design
- Check [API Reference](api-reference.md) for component details

## Troubleshooting

See [Troubleshooting Guide](troubleshooting.md) for common issues.
