# Usage Guide

How to run the bot, monitor performance, and interpret results.

## Running the Bot

### Single Test Run

Run one pipeline cycle and exit (useful for testing):

```bash
python src/main.py --once
```

Options:
- `--lookback N`: Check filings from last N days (default: 1)
- `--interval N`: Not used in `--once` mode

Example:
```bash
python src/main.py --once --lookback 7
```

### Continuous Operation

Start the bot to run continuously:

```bash
python src/main.py
```

The bot will:
- Poll SEC EDGAR every 60 seconds
- Process new 8-K filings
- Execute trades when signals meet criteria
- Send Discord alerts

Press `Ctrl+C` to stop gracefully.

### Custom Poll Interval

```bash
python src/main.py --interval 120  # Poll every 2 minutes
```

## Monitoring

### Console Output

The bot prints detailed logs to console:

```
============================================================
[16:32:32] Pipeline cycle starting...
============================================================
  [EDGAR] Fetching filings for LLY (CIK: 0000059478)...
  [EDGAR] Found 1 recent 8-K filings for LLY
    -> NEW: LLY: ELI LILLY & Co 8-K filed 2026-02-04 - 8-K
  [FILTER] LLY: score=40, dir=positive, keywords=[('phase 3', 'positive')]
  [GEMINI] Classified: Clinical Trial Result / Strong Positive (confidence: 95)
  [TRADE] Executing: buy 103 LLY @ $48.19 (OK: 5.0% of $100,000 portfolio)
  [TRADE] Order placed: abc123-def456
```

### Discord Alerts

Each trade triggers a Discord alert:

```
🟢 LONG $LLY | 103 shares @ $48.19
> Category: Clinical Trial Result
> Sentiment: Strong Positive (confidence: 95%)
> Headline: LLY: Eli Lilly Phase 3 trial...
> AI Reasoning: Successful Phase 3 trial indicates significant positive development...
```

System alerts are sent for:
- Bot startup/shutdown
- Daily summaries
- Errors
- Trading paused (daily loss limit hit)

### Database Queries

Query the SQLite database directly:

```bash
sqlite3 data/biotech_bot.db
```

**Recent trades:**
```sql
SELECT ticker, side, qty, price, created_at FROM trades ORDER BY created_at DESC LIMIT 10;
```

**Classification accuracy:**
```sql
SELECT sentiment, COUNT(*) as count, AVG(confidence) as avg_conf
FROM classified_events
GROUP BY sentiment;
```

**Daily LLM usage:**
```sql
SELECT date, model, call_count, total_input_tokens + total_output_tokens as total_tokens
FROM llm_usage
ORDER BY date DESC;
```

**Win rate:**
```sql
SELECT 
  COUNT(*) as total,
  SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
  AVG(pnl) as avg_pnl
FROM trades
WHERE pnl IS NOT NULL;
```

## Performance Analysis

### Backtest Report

After collecting some trades, generate a backtest report:

```bash
python src/backtest/report.py
```

Output:
```
============================================================
BACKTEST PERFORMANCE REPORT
============================================================
  Total Signals:       10
  Correct Predictions: 7
  Accuracy:            70.0%
  Win Rate (trades):   66.7%
  Avg Simulated P&L:   3.23%

  Sentiment             Count   Accuracy    Avg P&L
  -------------------- ------ ---------- ----------
  Strong Positive           3     100.0%      5.20%
  Strong Negative           2     100.0%      7.80%
  Weak Positive             2      50.0%      0.50%
```

### Synthetic Backtest (Historical Replay)

Replay 2 years of SEC filings through the classifier to see historical performance:

```bash
python src/backtest/synthetic_backtest.py --years 2 --max-classify 50
```

Options:
- `--years N`: Years of history to fetch (default: 2)
- `--max-filings N`: Max filings per ticker (default: 100)
- `--max-classify N`: Max Gemini classifications (default: 50, to preserve quota)
- `--fetch-text`: Fetch full filing text for better classification
- `--skip-filter`: Skip keyword filter, classify all filings (for testing)
- `--quiet`: Reduce verbosity

This generates a report showing:
- Historical accuracy by ticker and category
- Win rate and simulated P&L
- Performance breakdown by sentiment

### Profitability Calculator

Calculate minimum capital requirements:

```bash
python src/backtest/profitability.py --target 500 --api-cost 0
```

Output:
```
============================================================
PROFITABILITY ANALYSIS
============================================================
--- Trading Performance ---
  Total Trades:          15
  Win Rate:              66.7%
  Avg P&L per Trade:     $45.23
  Expected Monthly Return: 2.5%

--- Capital Requirements ---
  To Break Even:         $0.00
  For $500/mo income:    $20,000.00

--- Portfolio Size Scenarios ---
  $  1,000 -> $   25.00/mo  $    300.00/yr
  $  5,000 -> $  125.00/mo  $  1,500.00/yr
  $ 10,000 -> $  250.00/mo  $  3,000.00/yr
```

## Interpreting Results

### Keyword Filter Scores

- **Score 0-14**: Irrelevant, skipped (saves LLM cost)
- **Score 15-30**: Weak signal, may proceed to LLM
- **Score 30-50**: Moderate signal
- **Score 50+**: Strong signal (multiple keywords matched)

### Sentiment Classifications

- **Strong Positive**: High confidence positive catalyst (e.g., Phase 3 success)
- **Weak Positive**: Lower confidence or ambiguous positive
- **Neutral**: No clear directional signal
- **Weak Negative**: Lower confidence negative
- **Strong Negative**: High confidence negative (e.g., CRL, clinical hold)

### Confidence Scores

- **80-100**: Very high confidence (full position size)
- **50-79**: Medium confidence (60% position size)
- **30-49**: Low confidence (30% position size)
- **<30**: Very low confidence (may be skipped)

### Trade Execution

**Position Sizing Logic:**
1. Base: 5% of portfolio
2. Scaled by confidence: 30-100% of base
3. Scaled by sentiment strength: Strong = 100%, Weak = 60%, Neutral = 30%

**Example:**
- Portfolio: $100,000
- Base position: $5,000 (5%)
- Confidence: 95% → Full allocation: $5,000
- Sentiment: Strong Positive → Full allocation: $5,000
- Final: $5,000 / $48.19 = 103 shares

### Slippage Analysis

Slippage is logged 30 seconds after each trade:

```
[SLIPPAGE] LLY: signal=$48.19 -> after 30s=$48.05 (slippage: -0.29%)
```

**Interpretation:**
- **< 0.5%**: Excellent (paper trading is optimistic)
- **0.5-1%**: Good
- **1-2%**: Acceptable for low-volume stocks
- **> 2%**: High slippage (may indicate strategy doesn't work in practice)

## Best Practices

### 1. Start with Paper Trading

Run paper trading for **at least 4 weeks** before considering live trading:
- Collect 20+ trades
- Verify win rate > 50%
- Check slippage is reasonable (< 1%)
- Ensure profitability calculator shows positive returns

### 2. Monitor Daily

Check Discord alerts daily:
- Review each trade and its reasoning
- Verify classifications make sense
- Watch for errors or system alerts

### 3. Weekly Review

Every week:
- Run profitability calculator
- Check win rate trends
- Review LLM usage (stay within free tier)
- Adjust risk parameters if needed

### 4. Monthly Analysis

At month end:
- Generate full backtest report
- Calculate minimum capital requirements
- Review cross-company detection accuracy
- Decide if strategy is ready for live trading

## Common Workflows

### Testing a New Ticker

1. Add ticker to `config/watchlist.yaml` with CIK number
2. Run `python src/main.py --once --lookback 30`
3. Check if any filings were found
4. Review keyword filter scores
5. Check Discord for any classifications

### Adjusting Risk Parameters

1. Edit `src/trading/risk_manager.py`
2. Change `MAX_POSITION_PCT`, `MAX_DAILY_LOSS_PCT`, etc.
3. Restart bot: `python src/main.py`
4. Monitor for a few days to see impact

### Debugging Classification Issues

1. Check keyword filter: `python src/analysis/keyword_filter.py`
2. Test Gemini directly: `python src/analysis/gemini_classifier.py`
3. Review DB: `sqlite3 data/biotech_bot.db "SELECT * FROM classified_events ORDER BY id DESC LIMIT 5;"`
4. Check LLM usage: `sqlite3 data/biotech_bot.db "SELECT * FROM llm_usage ORDER BY date DESC;"`

## Stopping the Bot

**Graceful shutdown:**
- Press `Ctrl+C`
- Bot will finish current cycle
- Send shutdown alert to Discord
- Close database connections

**Force stop:**
- Press `Ctrl+C` twice
- Or kill process: `kill <PID>`

Database is safe (SQLite uses WAL mode, transactions are atomic).

## Next Steps

- Read [Architecture](architecture.md) to understand system design
- Review [API Reference](api-reference.md) for component details
- Check [Troubleshooting](troubleshooting.md) if issues arise
