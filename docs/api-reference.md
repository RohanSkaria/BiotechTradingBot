# API Reference

Component-level documentation for developers.

## Data Layer

### `src.data.edgar_8k`

**`poll_all_tickers(since_days=7, db_path=None, fetch_text=False)`**

Polls SEC EDGAR for new 8-K filings for all tickers in watchlist.

**Parameters:**
- `since_days` (int): Look back N days for filings (default: 7)
- `db_path` (str): Optional custom DB path
- `fetch_text` (bool): Fetch full filing text (default: False, saves API calls)

**Returns:** List of dicts with keys: `news_id`, `ticker`, `headline`, `date`, `accession`

**Example:**
```python
from src.data.edgar_8k import poll_all_tickers
events = poll_all_tickers(since_days=30, fetch_text=True)
```

**`fetch_company_filings(cik)`**

Fetches full filing history JSON for a CIK.

**Returns:** Dict with `filings.recent` containing form types, dates, accession numbers.

**`extract_8k_filings(data, since_days=7)`**

Extracts 8-K filings from EDGAR JSON, filters by date.

**Returns:** List of filing dicts with `date`, `accession_number`, `description`, `filing_url`.

## Database Layer

### `src.db.schema`

**`init_db(db_path=None)`**

Creates all database tables if they don't exist.

**Tables created:**
- `news_events`: Raw filings
- `classified_events`: LLM classifications
- `headline_hashes`: Dedup tracking
- `llm_usage`: Daily LLM call counter
- `trades`: Trade journal

**`get_connection(db_path=None)`**

Returns SQLite connection with row factory enabled.

### `src.db.storage`

**`insert_news_event(ticker, headline, filing_type, accession_number, ...)`**

Inserts a news event. Returns news_id or None if duplicate accession.

**`is_headline_seen(headline, db_path=None)`**

Checks if headline hash exists (dedup check).

**`insert_classification(news_id, category, sentiment, confidence, ...)`**

Inserts classification result and marks headline as seen. Returns classification_id.

**`increment_llm_usage(model, input_tokens=0, output_tokens=0)`**

Increments daily LLM usage counter.

**`get_daily_llm_calls(model=None)`**

Returns total LLM calls made today (all models or specific model).

**`insert_trade(ticker, side, qty, price, ...)`**

Inserts trade record. Returns trade_id.

**`get_recent_news(limit=20, ticker=None)`**

Returns recent news events, optionally filtered by ticker.

## Analysis Layer

### `src.analysis.keyword_filter`

**`filter_headline(headline)`**

Runs keyword filter on headline.

**Returns:** `FilterResult` object with:
- `is_relevant` (bool)
- `score` (int, 0-100)
- `direction` (str: "positive", "negative", "competitive", "neutral")
- `matched_keywords` (list of tuples: (keyword, direction))

**`should_classify(result)`**

Returns True if result.score >= RELEVANCE_THRESHOLD (15).

**Example:**
```python
from src.analysis.keyword_filter import filter_headline, should_classify

result = filter_headline("Eli Lilly Phase 3 trial meets primary endpoint")
if should_classify(result):
    print(f"Score: {result.score}, Direction: {result.direction}")
```

### `src.analysis.gemini_classifier`

**`classify_headline(headline, extra_context="", db_path=None)`**

Classifies headline using Gemini. Returns parsed dict or None if blocked.

**Returns:** Dict with keys:
- `category`: "Clinical Trial Result", "FDA Decision", etc.
- `sentiment`: "Strong Positive", "Weak Negative", etc.
- `confidence`: 0-100 integer
- `primary_ticker`: Main ticker affected
- `affected_tickers`: List of all affected tickers
- `reasoning`: One-sentence explanation
- `model_used`: Model that succeeded
- `token_count`: Estimated token usage

**`classify_and_store(news_id, headline, extra_context="", db_path=None)`**

Classifies and stores result in DB. Returns classification_id or None.

**Example:**
```python
from src.analysis.gemini_classifier import classify_and_store

class_id = classify_and_store(
    news_id=1,
    headline="LLY Phase 3 trial success",
    extra_context="Keyword filter detected: positive signal"
)
```

### `src.analysis.cost_guard`

**`can_classify(headline=None, db_path=None)`**

Checks if LLM call is allowed (daily limit, dedup).

**Returns:** Dict with `allowed` (bool), `reason` (str), `daily_calls` (int).

**`get_model_chain()`**

Returns list of models to try in order.

## Trading Layer

### `src.trading.executor`

**`get_account()`**

Returns Alpaca account dict with `portfolio_value`, `buying_power`, `status`.

**`get_portfolio_value()`**

Returns current portfolio value (float).

**`get_position(ticker)`**

Returns position dict for ticker, or None if no position.

**`get_all_positions()`**

Returns list of all open positions.

**`get_latest_price(ticker)`**

Returns latest trade price from Alpaca data API.

**`submit_order(ticker, qty, side, order_type="market", ...)`**

Submits order to Alpaca. Returns order dict or None.

**Parameters:**
- `side`: "buy" or "sell"
- `qty`: Number of shares (float, can be fractional)
- `order_type`: "market" (default), "limit", etc.

**`close_position(ticker)`**

Closes entire position. Returns close order dict or None.

### `src.trading.risk_manager`

**`calculate_position_size(ticker, price, sentiment="Neutral", confidence=50)`**

Calculates position size based on risk parameters.

**Returns:** Dict with:
- `qty` (int): Number of shares
- `dollar_amount` (float): Dollar value
- `allowed` (bool): Whether trade is allowed
- `reason` (str): Explanation
- `stop_loss_price` (float): Stop loss price
- `take_profit_price` (float): Take profit price

**`get_trade_side(sentiment)`**

Returns "buy", "sell", or "skip" based on sentiment.

**`should_stop_trading(db_path=None)`**

Checks if daily loss limit hit. Returns dict with `stop` (bool), `reason` (str).

### `src.trading.slippage_log`

**`log_slippage_async(trade_id, ticker, price_at_signal, delay_seconds=30)`**

Starts background thread that logs slippage after delay.

Updates `trades` table with `slippage_price_after_30s`.

## Alerts Layer

### `src.alerts.discord`

**`send_message(content, username="Biotech Bot")`**

Sends simple text message to Discord webhook.

**`send_trade_alert(ticker, side, qty, price, sentiment, category, confidence, headline, reasoning="")`**

Sends formatted trade alert.

**`send_system_alert(title, message)`**

Sends system-level alert (errors, summaries).

**`send_daily_summary(trades_today, pnl, portfolio_value, llm_calls)`**

Sends end-of-day summary.

## Backtest Layer

### `src.backtest.price_data`

**`get_price_around_event(ticker, event_date, days_before=2, days_after=3)`**

Fetches price data around event date using yfinance.

**Returns:** Dict with:
- `price_at_event`: Price on event date
- `price_day_before`: Price day before
- `pre_event_return`: Return from day before to event
- `returns`: Dict with "1d", "2d", "3d" returns

**`get_current_price(ticker)`**

Returns current/latest price.

### `src.backtest.evaluator`

**`evaluate_signal(ticker, event_date, predicted_sentiment, window="1d")`**

Compares predicted sentiment to actual price movement.

**Returns:** Dict with:
- `correct` (bool): Prediction was correct
- `actual_return_pct` (float): Actual return percentage
- `simulated_pnl_pct` (float): P&L if traded on signal

**`evaluate_batch(signals, window="1d")`**

Evaluates multiple signals. Returns list of evaluation dicts.

### `src.backtest.report`

**`generate_report(evaluations)`**

Generates performance report from evaluations.

**Returns:** Dict with accuracy, win rate, avg P&L by sentiment.

**`print_report(report)`**

Pretty-prints report to console.

### `src.backtest.synthetic_backtest`

**`fetch_historical_filings(years=2, max_per_ticker=50)`**

Fetches historical 8-K filings from SEC EDGAR for all watchlist tickers.

**Returns:** List of filing dicts with: ticker, date, headline, accession, filing_url, market_cap_tier, volatility.

**`run_synthetic_backtest(filings, fetch_text=False, max_classify=50, skip_filter=False, verbose=True)`**

Runs the synthetic backtest: classify historical filings and compare to actual price moves.

**Parameters:**
- `filings`: List of historical filing dicts
- `fetch_text`: Fetch full filing text (slower, more accurate)
- `max_classify`: Max Gemini classifications (to preserve quota)
- `skip_filter`: Skip keyword filter, classify all (for testing)
- `verbose`: Print progress

**Returns:** Dict with classifications, evaluations, and summary stats.

**CLI:**
```bash
python src/backtest/synthetic_backtest.py --years 2 --max-classify 50
```

### `src.backtest.profitability`

**`get_trading_stats(db_path=None)`**

Pulls aggregate stats from database.

**Returns:** Dict with total trades, wins, losses, avg P&L, LLM usage, etc.

**`calculate_profitability(stats, monthly_api_cost=0.0, target_monthly_income=500.0)`**

Calculates profitability metrics and capital requirements.

**Returns:** Dict with:
- `trades_per_month`: Estimated trades per month
- `expected_monthly_return_pct`: Projected monthly return
- `min_capital_breakeven`: Capital needed to break even
- `min_capital_for_target`: Capital needed for target income

**`print_profitability_report(stats, profitability)`**

Pretty-prints profitability analysis.

## Main Entry Point

### `src.main`

**`process_pipeline()`**

Runs one full pipeline cycle:
1. Poll EDGAR
2. Keyword filter
3. Gemini classify
4. Risk check
5. Execute trades
6. Send alerts

**`run_bot()`**

Main entry point. Runs continuous polling loop.

**`run_once()`**

Runs single cycle and exits (for testing).

**Command-line arguments:**
- `--once`: Single cycle mode
- `--interval N`: Poll interval in seconds
- `--lookback N`: EDGAR lookback in days

## Configuration

### `config/watchlist.yaml`

YAML file mapping tickers to CIK numbers and therapeutic areas.

**Structure:**
```yaml
tickers:
  LLY:
    name: "Eli Lilly"
    cik: "0000059478"
    therapeutic_area: "GLP-1 / Obesity"
    key_catalyst: "Oral Zepbound approval"
    market_cap_tier: "large"

therapeutic_areas:
  "GLP-1 / Obesity":
    - LLY
    - NVO
    - HIMS
```

## Error Handling

All components raise exceptions on errors. The main loop catches exceptions and:
- Logs to console
- Sends Discord system alert
- Continues processing (doesn't crash bot)

## Testing

### `tests.test_endpoints`

**`test_discord_webhook()`**

Tests Discord webhook connectivity.

**`test_alpaca_account()`**

Tests Alpaca API authentication and account access.

**`test_sec_edgar()`**

Tests SEC EDGAR API (Eli Lilly CIK).

**`test_gemini()`**

Tests Gemini API with sample classification prompt.

**`main()`**

Runs all tests and prints summary.

Run: `python tests/test_endpoints.py`
