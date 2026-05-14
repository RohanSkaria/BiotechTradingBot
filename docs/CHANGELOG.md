# Changelog

## [1.1.0] - 2026-02-06

### Added
- **Synthetic Backtest**: Replay 2 years of historical 8-K filings through classifier
  - `src/backtest/synthetic_backtest.py` - fetches historical filings from SEC EDGAR
  - Evaluates classification accuracy against actual price movements
  - Generates performance reports by ticker and category
- **Dynamic Stop-Loss**: Stop-loss now varies by market cap and volatility
  - Large cap, low vol: 8% (LLY, AMGN, GILD)
  - Large cap, medium vol: 10% (VRTX, REGN)
  - Mid cap, high vol: 15% (CRSP)
  - Take-profit maintains 2:1 reward:risk ratio
- **IOC Order Safety**: Orders now use Immediate-or-Cancel (IOC) time-in-force
  - Prevents slippage on low-volume biotech stocks
  - Order cancels if can't fill immediately at reasonable price

### Changed
- **Updated Watchlist**: Core biotech focus with 6 tickers (LLY, VRTX, CRSP, REGN, AMGN, GILD)
- Watchlist format changed from dict to list under `watchlists.core_biotech`
- Each ticker now includes `market_cap_tier` and `volatility` for risk management
- `submit_order()` now defaults to `time_in_force="ioc"` instead of `"day"`

### Technical
- `get_ticker_list()` helper function handles both old and new watchlist formats
- `get_stop_loss_pct()` calculates dynamic stop-loss from ticker info
- `get_ticker_info()` retrieves market cap and volatility from watchlist

---

## [1.0.0] - 2026-02-06

### Added
- Initial release of Biotech Trading Bot
- SEC EDGAR 8-K filing fetcher
- Three-tier classification pipeline (keyword filter → Gemini LLM → trade engine)
- Alpaca paper trading integration
- Discord webhook alerts
- SQLite database for event tracking
- Backtesting and profitability calculator
- Comprehensive documentation

### Features
- **Data Pipeline**: Polls SEC EDGAR for 8-K filings, extracts filing text
- **Keyword Filter**: Fast regex-based filter eliminates 70-80% of noise (zero cost)
- **Gemini Classifier**: LLM classification with 4-model fallback chain
- **Risk Management**: Position sizing (5% max), confidence scaling, daily loss limits
- **Trade Execution**: Alpaca REST API integration, slippage tracking
- **Cross-Company Detection**: Identifies competitors in same therapeutic area
- **Cost Optimization**: Gemini free tier + keyword filter = ~$0/month operating cost

### Technical Details
- Python 3.10+ required
- Dependencies: google-genai, alpaca-trade-api, yfinance, requests, apscheduler, pyyaml, python-dotenv
- Database: SQLite with WAL mode
- Rate limits: SEC EDGAR (10 req/s), Gemini (60 RPM free tier), Alpaca (paper trading limits)

### Documentation
- Setup guide
- Architecture deep dive
- Usage guide
- API reference
- Troubleshooting guide

### Known Limitations
- Paper trading only (not for live trading without additional validation)
- Requires manual curation of watchlist (CIK numbers)
- Slippage tracking is optimistic (paper trading fills are instant)
- Free tier Gemini quotas may be exhausted (falls back to paid models)

### Future Enhancements (v2)
- Options flow monitoring (Unusual Whales)
- Scientific abstract analysis
- Cross-company knowledge graph
- PostgreSQL migration for multi-instance deployment
- Insider trading signals (SEC Form 4)
