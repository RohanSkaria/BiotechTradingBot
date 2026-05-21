

**BIOTECH NEWS**  
**PAPER TRADING BOT**

System Architecture & Implementation Roadmap

|  |
| :---- |

Prepared for Rohan

February 2026

Paper Trading • Alpaca API • AI-Powered Sentiment

# **Executive Summary**

This report outlines a complete architecture for building an AI-powered biotech news trading bot for paper trading. The system uses a tiered inference pipeline to minimize API costs, processes biotech-specific news categories, and executes trades via Alpaca’s paper trading API. All recommendations are informed by peer-reviewed research on biotech stock reactions to clinical trial announcements and competitive market events.

The system is designed for learning and experimentation. It is **not** intended for live trading without significant additional validation, risk controls, and regulatory understanding.

| Key Research Findings That Shape This Architecture • FinBERT predicts biotech news sentiment with only \~67% accuracy (barely better than coin flip) • Early-stage biotech companies show dramatically larger stock moves than big pharma on news • Negative news creates \-13% abnormal returns vs only \+6% for positive news (asymmetric) • 74% of cumulative abnormal returns occur BEFORE the public announcement (information leakage) • Large-cap pharma is NOT immune to competitive/pricing threats (e.g., LLY \-7% on HIMS news, Feb 2026\) |
| :---- |

> **Editor's note (added May 2026):** The four bulleted statistics above came from the LLM that drafted this report and were not traced back to primary sources at the time of writing. On subsequent verification, the qualitative direction of every claim is supported by peer-reviewed research, but the specific percentages are not. The actual peer-reviewed numbers are:
>
> - FinBERT specifically achieves ~67% on biotech press releases — **no source found**. The defensible statement is that general financial sentiment models underperform on biomedical text, which is why domain-specific models like GAN-BioBERT exist ([Frontiers in Digital Health, 2022](https://www.frontiersin.org/journals/digital-health/articles/10.3389/fdgth.2022.878369/full)).
> - Asymmetric returns of -13% / +6% — actual published numbers are **+0.8% positive vs. -2.0% negative** median announcement-day abnormal returns, a ~2.5x asymmetry ([PLOS ONE](https://journals.plos.org/plosone/article?id=10.1371%2Fjournal.pone.0071966)).
> - "74% of cumulative abnormal returns before announcement" — **no source found**. A real pre-announcement figure is the **13.7% mean run-up** in the 120 trading days before positive Phase III oncology announcements (Rothenstein et al., 2011, [PubMed 21949081](https://pubmed.ncbi.nlm.nih.gov/21949081/)).
>
> Updated numbers and citations now live in `docs/README.md` and `docs/architecture.md`. This file is preserved as the original report.

# **1\. System Architecture**

The system follows a three-tier pipeline designed to balance cost efficiency with analytical accuracy. Each tier filters and enriches the signal before passing it to the next.

## **1.1 Tier 0: Data Sources (The Foundation)**

Before any analysis begins, you need reliable, fast data feeds. This is the most critical and often overlooked piece of the system. Without good data, no amount of AI sophistication will help.

| Source | Type | Latency | Best For |
| :---- | :---- | :---- | :---- |
| SEC EDGAR 8-K | Regulatory Filing | First legal disclosure | Trial results, offerings |
| BioPharmCatalyst | Structured Catalyst | Near real-time | PDUFA dates, pipeline |
| Benzinga Pro API | News Wire | Fast (seconds) | Breaking headlines |
| ClinicalTrials.gov | Trial Registry | Slow (days) | Pre-catalyst monitoring |
| Unusual Whales / CBOE | Options Flow | Real-time | Leading indicator (v2) |

**Recommendation:** Start with BioPharmCatalyst (structured, research-validated, used in peer-reviewed ML frameworks) and SEC EDGAR 8-K RSS feed. Add Benzinga as budget allows.

## **1.2 Tier 1: Keyword Filter (Cost: $0)**

A simple Python script using regex or basic string matching. No NLP libraries needed. This should discard 70-80% of irrelevant headlines immediately.

### **Positive Catalyst Keywords**

* "Phase 3", "FDA Approval", "PDUFA", "Primary Endpoint Met", "Breakthrough Therapy", "Fast Track", "Orphan Drug", "Accelerated Approval", "Priority Review", "Complete Response" (contextual)

### **Negative Catalyst Keywords**

* "CRL" (Complete Response Letter), "Clinical Hold", "Partial Hold", "Safety Signal", "Adverse Events", "Dilutive Offering", "Secondary Offering", "Shelf Registration", "Failed Endpoint", "Discontinued"

### **Competitive / Pricing Keywords (Added Post-LLY Analysis)**

* "Price War", "Compounded", "Generic", "Biosimilar", "Patent Expiry", "LOE" (Loss of Exclusivity), "Undercut", "Cheaper Alternative"

| Implementation Note Do NOT use SpaCy or heavy NLP for Tier 1\. A Python set lookup or compiled regex pattern is faster, simpler, and easier to maintain. Save complexity for where it matters. |
| :---- |

## **1.3 Tier 2: LLM Classification (Skip FinBERT)**

Based on published research showing FinBERT achieves only \~67% accuracy on biotech press releases, we recommend **skipping FinBERT entirely** and going directly to a cheap LLM. Claude Haiku or GPT-4o-mini costs fractions of a cent per call and will significantly outperform FinBERT on domain-specific biotech language.

The LLM should classify each headline along two dimensions:

* **News Category:** Clinical Trial Result, FDA Decision, Competitive Threat, Offering/Dilution, M\&A, Earnings, Partnership

* **Sentiment \+ Confidence:** Strong Positive, Weak Positive, Neutral, Weak Negative, Strong Negative (with a 0-100 confidence score)

* **Affected Tickers:** Not just the company named, but therapeutically related companies (critical for cross-company events like the HIMS/LLY scenario)

| Why This Matters On Feb 5, 2026, Hims & Hers announced a $49 compounded Wegovy pill. The news was about HIMS, but LLY dropped 7% and NVO dropped 7%. A ticker-matched system would have completely missed this. Your LLM prompt must explicitly ask: "Which other tickers in the GLP-1 / obesity / same therapeutic area would be affected?" |
| :---- |

## **1.4 Tier 3: Trade Decision Engine**

This tier converts the classified news into actual trade signals. It should incorporate:

* The news category and sentiment from Tier 2

* Company size / drug portfolio size (single-drug biotechs move more)

* Current position (if any) in the ticker

* Time of day (pre-market news behaves differently than intraday)

* A hardcoded risk limit per trade (e.g., max 5% of portfolio)

# **2\. Biotech News Categories**

The type of news matters more than generic sentiment. Your system must classify news into specific categories, each with different expected behavior.

| News Type | AI Focus | Expected Move | Direction Bias |
| :---- | :---- | :---- | :---- |
| Phase 3 Results | Primary endpoint met? p-value? | Extreme (binary) | Asymmetric: \-13% / \+6% |
| FDA PDUFA | Approval vs CRL | High | Binary outcome |
| Competitive Threat | Price undercut? Market share risk? | High | Negative for incumbent |
| Secondary Offering | Dilution %, cash runway impact | Moderate-High | Almost always negative |
| M\&A / Acquisition | Acquirer vs Target, premium | Moderate-High | Positive for target |
| Earnings | R\&D spend, cash runway, guidance | Moderate | Context dependent |
| Partnership / License | Deal terms, milestone payments | Low-Moderate | Usually positive |
| Patent / IP | Expiry date, litigation outcome | Moderate | Negative on expiry |

| Research Insight Negative news creates roughly 2x the abnormal return magnitude of positive news. Your bot should be biased toward detecting and acting on negative catalysts — the short side has a statistically stronger signal in biotech. |
| :---- |

# **3\. Watchlist Strategy**

Your watchlist should be organized by therapeutic area, not just individual tickers. This enables cross-company event detection like the HIMS → LLY contagion.

## **Core Watchlist**

| Ticker | Company | Therapeutic Area | Key Catalyst |
| :---- | :---- | :---- | :---- |
| $LLY | Eli Lilly | GLP-1 / Obesity | Oral Zepbound approval |
| $NVO | Novo Nordisk | GLP-1 / Obesity | Wegovy pill ramp, pricing |
| $CRSP | CRISPR Therapeutics | Gene Editing | Casgevy commercial ramp |
| $VRTX | Vertex Pharma | CF / Pain | Suzetrigine launch, pipeline |
| $RXRX | Recursion Pharma | AI Drug Discovery | Pipeline validation (LOW VOL\!) |
| $HIMS | Hims & Hers | GLP-1 Disruption | Compounding legal battle |

**Liquidity Warning:** $RXRX has low daily volume. Paper trading fills will not reflect real market conditions. Assume 1-2% slippage on any trade, and consider excluding it from automated execution until you add slippage modeling.

# **4\. Paper Trading Implementation**

## **4.1 Environment Setup**

* **Broker:** Alpaca Paper Trading (free, same API as live, well-documented Python SDK)

* **Language:** Python 3.10+ (alpaca-trade-api, requests, anthropic or openai SDK)

* **Database:** SQLite for trade logs, news events, and performance tracking

* **Scheduler:** cron job or APScheduler to poll news sources every 30-60 seconds during market hours

* **Monitoring:** Simple Discord/Slack webhook for trade alerts

## **4.2 Implementation Phases**

### **Phase 1: Data Pipeline (Week 1-2)**

Build the news ingestion system first, without any trading logic. Focus on reliably capturing and storing biotech headlines with timestamps, tickers, and raw text.

* Set up BioPharmCatalyst scraping or API integration

* Set up SEC EDGAR 8-K RSS feed parser

* Build SQLite schema: news\_events(id, timestamp, source, headline, tickers, raw\_text)

* Run for 1 week passively, just collecting data

### **Phase 2: Classification Pipeline (Week 3-4)**

Add the keyword filter and LLM classification. Still no trading.

* Implement Tier 1 keyword filter with positive, negative, and competitive keyword sets

* Build Tier 2 LLM prompt (Claude Haiku or GPT-4o-mini)

* Store classifications: classified\_events(id, news\_id, category, sentiment, confidence, affected\_tickers)

* Manually review classifications for 1-2 weeks to tune prompts and keywords

### **Phase 3: Backtesting (Week 5-6)**

Before connecting to Alpaca, backtest against historical data.

* Pull 3-6 months of historical news from your data sources

* Run through your classification pipeline

* Compare predicted sentiment against actual price movements (Yahoo Finance API)

* Calculate: accuracy, precision/recall by category, average return per signal

* **Key metric:** Does your “Strong Negative” classification actually correlate with negative price movement within 1 hour? If not, tune before proceeding.

### **Phase 4: Paper Trading (Week 7+)**

Only now connect to Alpaca paper trading.

* Set up Alpaca paper trading account and API keys

* Implement trade execution with position sizing (max 5% portfolio per trade)

* Add stop-loss and take-profit logic

* Log every trade with the news event that triggered it

* Run for 4+ weeks, tracking performance daily

| Slippage Modeling Alpaca paper trading gives you instant fills at quoted prices. Real markets don’t work this way, especially for low-volume biotech stocks. For every paper trade, log the quoted price at time of signal AND the price 30 seconds later. The difference is your estimated slippage. If slippage consistently exceeds your expected return, the strategy doesn’t work in practice. |
| :---- |

# **5\. Recommended Tech Stack**

| Component | Technology | Notes |
| :---- | :---- | :---- |
| Language | Python 3.10+ | Best ecosystem for finance \+ ML |
| Broker API | alpaca-trade-api | Paper \+ live trading, same API |
| LLM (Tier 2\) | Claude Haiku / GPT-4o-mini | \<$0.001 per classification |
| Database | SQLite → PostgreSQL | Start simple, migrate if needed |
| News Source | BioPharmCatalyst \+ EDGAR | Structured \+ regulatory |
| Backtesting | Custom \+ yfinance | Historical price data from Yahoo |
| Alerts | Discord webhook | Free, instant mobile notifications |
| Deployment | AWS EC2 t3.micro or local | Free tier eligible |

# **6\. Known Risks & Limitations**

## **6.1 Market Structure Risks**

* **Information leakage:** Research shows 74% of abnormal returns happen before the public announcement. By the time your bot reads the headline, most of the move may be over.

* **Speed disadvantage:** Hedge funds like RA Capital and OrbiMed have teams of PhDs and co-located servers. Your pipeline latency (30-60 seconds) means you’re not competing on speed.

* **Paper vs real fills:** Paper trading ignores slippage, spread, and liquidity constraints. Performance will degrade in live trading.

## **6.2 Technical Risks**

* **LLM hallucination:** The LLM may misclassify ambiguous headlines or invent affected tickers. Always validate LLM output against your watchlist.

* **Data source downtime:** If your news feed goes down during a catalyst, you miss the trade. Build redundancy with multiple sources.

* **Overfitting to backtest:** Biotech catalysts are low-frequency events. A 3-month backtest may only have 10-20 tradeable signals. Be cautious about drawing conclusions from small samples.

## **6.3 Regulatory Risks**

* **Compounding legality:** The HIMS/LLY situation is evolving. Novo Nordisk is suing. Regulatory outcomes here are unpredictable and could swing the entire GLP-1 thesis.

* **FDA policy changes:** Accelerated approval pathways, pricing regulations, and patent reform could change the market dynamics your bot relies on.

# **7\. Future Enhancements (v2)**

These features are worth building once the core system is validated in paper trading.

* **Scientific Abstract Analysis:** Leverage your biomedical background to parse clinical trial abstracts and predict results before the market prices them in. This is your unique edge over generic quant traders.

* **Options Flow Monitoring:** Unusual options activity (large put purchases, call sweeps) on biotech stocks often precedes catalysts. This could serve as a leading indicator for your Tier 1 filter.

* **Cross-Company Knowledge Graph:** Build a graph connecting companies by therapeutic area, mechanism of action, and competitive relationships. This automates the “HIMS news affects LLY” logic.

* **Insider Trading Signals:** Monitor SEC Form 4 filings for insider buying/selling. Large insider sells during “progress” press releases is a red flag.

| Getting Started Checklist 1\. Create Alpaca paper trading account (alpaca.markets) 2\. Get API keys for your chosen LLM (Anthropic or OpenAI) 3\. Set up Python project: pip install alpaca-trade-api anthropic yfinance 4\. Build Phase 1 data pipeline — just collect news for 1 week 5\. Add keyword filter \+ LLM classification (Phase 2\) 6\. Backtest against 3+ months of historical data (Phase 3\) 7\. Connect to Alpaca paper trading only after backtesting validates the signal (Phase 4\) 8\. Run paper trading for 4+ weeks before evaluating performance Total estimated time to first paper trade: 6-7 weeks Estimated monthly cost (paper trading phase): $5-15 in LLM API calls |
| :---- |

