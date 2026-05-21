# Biotech Trading Bot Documentation

An AI-powered paper trading bot that monitors SEC EDGAR 8-K filings for biotech catalysts, classifies them using Google Gemini, and executes trades via Alpaca paper trading.

## Quick Links

- [Setup Guide](setup.md) - Installation and configuration
- [Architecture](architecture.md) - System design and data flow
- [Usage Guide](usage.md) - Running the bot and interpreting results
- [API Reference](api-reference.md) - Component documentation
- [Troubleshooting](troubleshooting.md) - Common issues and solutions

## Overview

The bot follows a three-tier pipeline designed to minimize LLM costs while maximizing signal quality:

1. **Tier 0: Data Sources** - SEC EDGAR 8-K filings (free, legally required disclosures)
2. **Tier 1: Keyword Filter** - Fast regex-based filter (zero cost) that eliminates 70-80% of noise
3. **Tier 2: Gemini LLM** - Classifies remaining headlines into categories, sentiment, and affected tickers
4. **Tier 3: Trade Engine** - Risk management, position sizing, and execution via Alpaca

## Key Features

- **Cost-Efficient**: Gemini free tier (60 RPM) + keyword pre-filter = ~$0/month operating cost
- **Cross-Company Detection**: Identifies competitors in same therapeutic area
- **Dynamic Risk Management**: Stop-loss scaled by market cap + volatility (8-15%)
- **Slippage Protection**: IOC (Immediate or Cancel) orders prevent bad fills on volatile stocks
- **Synthetic Backtesting**: Replay 2 years of historical filings to validate strategy
- **Research-Backed**: Based on peer-reviewed findings on biotech stock reactions
- **Paper Trading First**: Full validation before risking real capital

## Current Watchlist

| Ticker | Company | Focus | Stop-Loss |
|--------|---------|-------|-----------|
| LLY | Eli Lilly | Obesity/Alzheimer's | 8% |
| VRTX | Vertex | Rare Disease/Pain | 10% |
| CRSP | CRISPR Therapeutics | Gene Editing | 15% |
| REGN | Regeneron | Antibodies/Oncology | 10% |
| AMGN | Amgen | Obesity/Immunology | 8% |
| GILD | Gilead | HIV/Oncology | 8% |

## Project Structure

```
BIOTECH-TRADING-BOT/
├── config/
│   └── watchlist.yaml          # Ticker → CIK mapping, therapeutic areas
├── src/
│   ├── data/
│   │   └── edgar_8k.py         # SEC EDGAR 8-K fetcher
│   ├── db/
│   │   ├── schema.py           # SQLite schema
│   │   └── storage.py           # DB helpers
│   ├── analysis/
│   │   ├── keyword_filter.py   # Tier 1: Regex filter
│   │   ├── gemini_classifier.py # Tier 2: LLM classification
│   │   └── cost_guard.py       # LLM cost controls
│   ├── backtest/
│   │   ├── price_data.py       # Historical price fetcher
│   │   ├── evaluator.py        # Signal evaluation
│   │   ├── report.py           # Performance reports
│   │   └── profitability.py    # Capital requirement calculator
│   ├── trading/
│   │   ├── executor.py         # Alpaca order execution
│   │   ├── risk_manager.py     # Position sizing, limits
│   │   └── slippage_log.py     # Slippage tracking
│   ├── alerts/
│   │   └── discord.py          # Discord webhook notifications
│   └── main.py                 # Main scheduler
├── tests/
│   └── test_endpoints.py       # Endpoint verification
└── docs/                       # This documentation
```

## Quick Start

1. **Install dependencies:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Configure environment variables** (`.env` file):
   ```
   ALPACA_KEY=your_paper_key
   ALPACA_SECRET=your_paper_secret
   GEMINI_API_KEY=your_gemini_key
   ```

3. **Verify endpoints:**
   ```bash
   python tests/test_endpoints.py
   ```

4. **Run a single pipeline cycle:**
   ```bash
   python src/main.py --once
   ```

5. **Start the bot (continuous polling):**
   ```bash
   python src/main.py
   ```

## Cost Analysis

With Gemini free tier, operating costs are effectively **$0/month**:
- Gemini Flash: 60 requests/minute, 1M tokens/minute (free tier)
- SEC EDGAR: Free, no API key required
- Alpaca Paper Trading: Free
- Discord Webhooks: Free

At typical paper trading volume (10-20 classifications/day), you'll stay well within free tier limits.

## Research Foundation

The bot's architecture is informed by peer-reviewed research on biotech stock reactions:

- **FinBERT is built for general corporate finance, not biotech.** Researchers have built entirely separate biomedical sentiment models (e.g., GAN-BioBERT) precisely because general financial language models miss the clinical and biochemical vocabulary required to parse trial endpoints and efficacy metrics. Source: [Validating GAN-BioBERT — Frontiers in Digital Health, 2022](https://www.frontiersin.org/journals/digital-health/articles/10.3389/fdgth.2022.878369/full).
- **Asymmetric returns.** A PLOS ONE event study of large biopharma announcements found median announcement-day cumulative abnormal returns of **+0.8% on positive clinical events vs. -2.0% on negative events** — a ~2.5x downside asymmetry. Source: [Stock Market Returns and Clinical Trial Results — PLOS ONE](https://journals.plos.org/plosone/article?id=10.1371%2Fjournal.pone.0071966).
- **Pre-announcement run-up.** A PubMed-indexed oncology event study found companies reporting positive Phase III trials had stock prices **already up 13.7% on average in the 120 trading days before the public announcement** (vs. -0.7% for companies reporting negative trials, p = 0.09; the 60-day post-hoc window of +9.4% vs. -4.5% was significant at p = 0.03). Most of the alpha is captured by institutional capital before EDGAR ever fires. Source: [Rothenstein et al., 2011 — PubMed 21949081](https://pubmed.ncbi.nlm.nih.gov/21949081/).
- **Cross-company effects.** Large-cap pharma is not immune to competitive threats (e.g., LLY -7% on HIMS compounded-Wegovy news, Feb 2026). Observational, not peer-reviewed.

## Disclaimer

**This bot is for paper trading and learning purposes only.** It is NOT intended for live trading without:
- Significant additional validation
- Risk controls beyond what's implemented
- Regulatory understanding (SEC, FINRA rules)
- Professional financial advice

Past performance does not guarantee future results. Biotech trading involves substantial risk.

## License

See LICENSE file (if applicable).

## Support

For issues, see [Troubleshooting](troubleshooting.md) or open an issue in the repository.
