# Troubleshooting Guide

Common issues and solutions.

## Setup Issues

### "ModuleNotFoundError: No module named 'src'"

**Problem:** Python can't find the `src` module.

**Solution:**
```bash
# Make sure you're in the project root directory
cd /path/to/BIOTECH-TRADING-BOT

# Run from project root
python src/main.py
```

Or add project root to PYTHONPATH:
```bash
export PYTHONPATH="${PYTHONPATH}:/path/to/BIOTECH-TRADING-BOT"
```

### "ALPACA_KEY not set in .env"

**Problem:** Environment variables not loaded.

**Solution:**
1. Check `.env` file exists in project root
2. Verify format: `ALPACA_KEY=your_key_here` (no spaces around `=`)
3. Restart terminal/IDE after creating `.env`
4. Or load manually: `export ALPACA_KEY=your_key`

### "Database locked" or SQLite errors

**Problem:** Multiple processes accessing database simultaneously.

**Solution:**
- Only run one instance of the bot at a time
- If crashed, wait a few seconds for locks to clear
- Check for zombie processes: `ps aux | grep python`

## API Issues

### Discord webhook returns 401/404

**Problem:** Webhook URL is invalid or expired.

**Solution:**
1. Go to Discord → Server Settings → Integrations → Webhooks
2. Create new webhook or regenerate URL
3. Update `DISCORD_WEBHOOK_URL` in `src/alerts/discord.py`

### Alpaca returns 401 Unauthorized

**Problem:** Invalid API keys.

**Solution:**
1. Verify keys in `.env` match Alpaca dashboard
2. Check you're using **paper trading** keys (not live)
3. Regenerate keys in Alpaca dashboard if needed
4. Ensure no extra spaces/newlines in `.env` file

### SEC EDGAR returns 403 Forbidden

**Problem:** Missing or invalid User-Agent header.

**Solution:**
- User-Agent is set in `src/data/edgar_8k.py` as `'Rohan Skaria (rohan.skaria@email.com)'`
- SEC requires a valid email in User-Agent
- Update the email if you want to use your own

### Gemini returns 429 Quota Exceeded

**Problem:** Free tier quota exhausted.

**Solution:**
1. Check quota at [Google AI Studio](https://aistudio.google.com)
2. Wait for quota reset (usually daily)
3. Bot automatically falls back to next model in chain
4. If all models exhausted, wait 24 hours or enable billing

**Models tried in order:**
- `gemini-2.0-flash` (free tier: 60 RPM)
- `gemini-2.0-flash-lite` (free tier: 60 RPM)
- `gemini-2.5-flash-lite` (free tier: 60 RPM)
- `gemini-2.5-flash` (paid)

### Gemini returns 503 Unavailable

**Problem:** Model is temporarily overloaded.

**Solution:**
- Bot automatically retries after 3 seconds
- If still fails, falls back to next model
- Usually resolves within minutes

## Classification Issues

### All headlines filtered out (score=0)

**Problem:** Keyword filter too strict, or filings don't contain catalyst keywords.

**Solution:**
- This is **normal** for routine 8-K filings (board changes, financial reporting)
- Real catalysts (Phase 3 results, FDA decisions) will have keywords
- Check raw filing text: `sqlite3 data/biotech_bot.db "SELECT raw_text FROM news_events WHERE id=1;"`
- If filing text contains keywords but headline doesn't, bot checks raw text automatically

### Gemini returns invalid JSON

**Problem:** LLM output doesn't parse as JSON.

**Solution:**
- Bot logs raw response to console
- Check console output for actual response
- Usually caused by markdown fences (` ```json ... ``` `) - bot strips these automatically
- If persists, may need to adjust prompt in `src/analysis/gemini_classifier.py`

### "Headline already classified (dedup)"

**Problem:** Same headline was already processed.

**Solution:**
- This is **expected behavior** - prevents duplicate LLM calls
- If you want to re-classify, delete from DB:
  ```sql
  DELETE FROM headline_hashes WHERE hash = '...';
  DELETE FROM classified_events WHERE news_id = ...;
  ```

## Trading Issues

### "Cannot get price for TICKER"

**Problem:** Alpaca data API can't fetch price.

**Solution:**
1. Check ticker symbol is correct (uppercase, no spaces)
2. Verify market is open (prices unavailable outside market hours)
3. Check Alpaca supports the ticker (some OTC stocks not available)
4. Try manually: `python -c "from src.trading.executor import get_latest_price; print(get_latest_price('LLY'))"`

### Order cancelled immediately (IOC)

**Problem:** Orders are getting cancelled instead of filled.

**Solution:**
- This is **expected behavior** with IOC (Immediate or Cancel) orders
- IOC orders cancel if they can't fill immediately at a reasonable price
- This protects you from slippage on volatile biotech stocks
- If market is illiquid, the order may not fill
- Check Alpaca dashboard for order status: "canceled" vs "filled"
- For paper trading, most orders will fill; in live trading, expect some cancellations

### Order fails with "insufficient buying power"

**Problem:** Not enough cash in paper account.

**Solution:**
- Paper account starts with $100,000
- Check: `python -c "from src.trading.executor import get_account; print(get_account())"`
- If low, reset paper account in Alpaca dashboard (or wait for positions to close)

### "Max open positions reached"

**Problem:** Already have 5 open positions (default limit).

**Solution:**
- Close some positions manually in Alpaca dashboard
- Or increase `MAX_OPEN_POSITIONS` in `src/trading/risk_manager.py`
- Bot will automatically close positions when stop-loss/take-profit hit

### Orders execute but no Discord alert

**Problem:** Discord webhook failed silently.

**Solution:**
1. Check webhook URL is correct
2. Test manually: `python -c "from src.alerts.discord import send_message; send_message('test')"`
3. Check Discord server for webhook status
4. Bot continues even if Discord fails (non-critical)

## Performance Issues

### Bot is slow / high latency

**Problem:** Network delays or too many API calls.

**Solution:**
- Increase poll interval: `python src/main.py --interval 120`
- Reduce lookback window: `--lookback 1` (only check last day)
- Disable text fetching: Edit `src/main.py`, set `fetch_text=False` in `poll_all_tickers()`

### Database file grows large

**Problem:** SQLite file accumulating data.

**Solution:**
- Normal for long-running bot
- SQLite handles GB-sized files fine
- To clean old data:
  ```sql
  DELETE FROM news_events WHERE timestamp < date('now', '-90 days');
  DELETE FROM classified_events WHERE created_at < date('now', '-90 days');
  ```

### High LLM usage / hitting daily limit

**Problem:** Too many Gemini calls.

**Solution:**
- Check daily usage: `sqlite3 data/biotech_bot.db "SELECT * FROM llm_usage ORDER BY date DESC;"`
- Increase `RELEVANCE_THRESHOLD` in `src/analysis/keyword_filter.py` (default: 15)
- Increase `DAILY_CALL_LIMIT` in `src/analysis/cost_guard.py` if needed
- Review keyword filter - may need to add more keywords to catch more signals early

## Data Issues

### No filings found for ticker

**Problem:** Ticker not in watchlist or CIK incorrect.

**Solution:**
1. Check `config/watchlist.yaml` has the ticker
2. Verify CIK number: [SEC EDGAR Company Search](https://www.sec.gov/edgar/searchedgar/companysearch.html)
3. Test manually: `python -c "from src.data.edgar_8k import fetch_company_filings; print(fetch_company_filings('0000059478'))"`

### Filings found but no trades executed

**Problem:** Classifications don't meet trading criteria.

**Solution:**
1. Check keyword filter scores: Look for `[FILTER]` logs
2. Check classifications: `sqlite3 data/biotech_bot.db "SELECT * FROM classified_events ORDER BY id DESC LIMIT 5;"`
3. Check risk manager blocks: Look for `[TRADE] blocked:` logs
4. Verify sentiment is not "Neutral" (skipped)

### Slippage always zero

**Problem:** Slippage logger not running or price unchanged.

**Solution:**
- Slippage is logged 30 seconds after trade
- Check `trades` table: `SELECT slippage_price_at_signal, slippage_price_after_30s FROM trades;`
- If NULL, slippage logger may have failed (non-critical, doesn't block trading)

## Getting Help

### Check Logs

All errors are logged to console. Look for:
- `[ERROR]` prefix
- Stack traces
- API error messages

### Database Inspection

```bash
sqlite3 data/biotech_bot.db

# Check recent events
SELECT * FROM news_events ORDER BY id DESC LIMIT 5;

# Check classifications
SELECT * FROM classified_events ORDER BY id DESC LIMIT 5;

# Check trades
SELECT * FROM trades ORDER BY id DESC LIMIT 5;

# Check LLM usage
SELECT * FROM llm_usage ORDER BY date DESC;
```

### Run Tests

```bash
# Test all endpoints
python tests/test_endpoints.py

# Test keyword filter
python src/analysis/keyword_filter.py

# Test Gemini (uses 1 API call)
python -c "from src.analysis.gemini_classifier import classify_headline; print(classify_headline('LLY Phase 3 success'))"
```

### Common Debug Commands

```bash
# Check environment variables
python -c "import os; from dotenv import load_dotenv; load_dotenv(); print('ALPACA_KEY:', os.getenv('ALPACA_KEY')[:4] + '...')"

# Check database
python -c "from src.db.schema import get_connection; conn = get_connection(); print(conn.execute('SELECT COUNT(*) FROM news_events').fetchone()[0])"

# Check Alpaca account
python -c "from src.trading.executor import get_account; print(get_account())"

# Check Gemini quota
python -c "from src.analysis.cost_guard import get_daily_llm_calls; print(f'Calls today: {get_daily_llm_calls()}')"
```

## Still Stuck?

1. Check [Architecture](architecture.md) to understand system flow
2. Review [API Reference](api-reference.md) for component details
3. Search codebase for error message
4. Open an issue with:
   - Error message
   - Steps to reproduce
   - Relevant logs
   - Database state (if applicable)
