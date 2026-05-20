---
name: biotech-weekly-brief
description: Produces a weekly biotech catalyst brief with high-conviction trade ideas, including catalyst timing, thesis, and risk flags. Use when asked for weekly biotech setup reports, catalyst calendars, or Monday pre-market biotech summaries.
---

# Biotech Weekly Brief Skill

## Objective

Create a Monday pre-market report for a supplied ticker list that identifies:
- Which names have actionable catalysts this week
- Direction (`long`, `short`, or `skip`)
- Conviction score (0-100)
- Evidence-backed thesis and invalidation risks

## Input Assumptions

- User query includes the ticker list and target week.
- If week is omitted, assume current trading week (starting Monday in ET).
- Focus on biotech/biopharma catalyst sensitivity (FDA events, trial readouts, safety updates, financing risk).

## Step 0: Watchlist Management and Discovery (run BEFORE per-ticker analysis)

This step keeps the universe fresh and discovers pioneering biotech candidates for future briefs.

1. **Expire stale entries**
   - Call `manage_watchlist` with `action='expire_stale'` (default 14-day threshold).
   - Only touches rows with `discovery_source='dexter_brief'`; manual and other-source entries are never modified.

2. **Inspect current state**
   - Call `manage_watchlist` with `action='list_active'` to see what is already tracked and avoid duplicates.

3. **Discover pioneering biotech candidates** (always run, every brief)
   - Use a combination of:
     - `stock_screener` for biotech sector filters (e.g., market cap >= $300M, biotech industry, etc.)
     - `web_search` queries such as "biotech PDUFA calendar next 14 days", "FDA advisory committee biotech", "Phase 3 readout biotech [month] [year]"
     - `get_yahoo_finance_data` with `action='search'` for thematic name discovery
   - Verify candidates against primary sources (company press releases, FDA calendars, ClinicalTrials.gov) before adding.

4. **Add high-quality candidates**
   - For each verified candidate with a near-term catalyst, call `manage_watchlist` with `action='add'` and provide `ticker`, optional `company_name`, `expected_catalyst` (including the date), `market_cap_tier`, and `priority`.
   - The tool deduplicates by ticker; re-adding refreshes the existing row's TTL without changing its original `discovery_source`.
   - Be conservative: only add candidates with a verifiable, dated catalyst in the next ~14 days.

5. **Analyze**
   - Proceed with the per-ticker workflow below for the tickers the user supplied.
   - If you discovered a ticker with a catalyst THIS WEEK that you also want to surface immediately, include it in your `signals` JSON output. Otherwise, newly-discovered tickers will surface in next week's brief automatically.

## Mandatory Workflow (Per Ticker)

1. **Filings and catalyst context**
   - Use `read_filings` for recent 8-K/10-Q/10-K signal extraction.
   - Look for explicit language around trial endpoints, timing updates, holds, CRLs, dilution, and cash runway.

2. **Clinical trial verification**
   - Use `web_search`, `web_fetch`, and/or `browser` to verify trial-status updates and catalyst dates from trustworthy sources.
   - Prefer primary sources (company PR, FDA calendar references, conference schedules).

3. **News flow**
   - Pull catalyst-focused news using:
     - `web_search` (broad web grounding)
     - `get_alpaca_market_data` with `action='news'` for additional coverage

4. **Price action and tape context**
   - Primary: `get_market_data` for recent price behavior.
   - If unavailable or credits exhausted: `get_alpaca_market_data` with `action='bars'` and `action='latest_quote'`.

5. **Fundamentals and balance-sheet risk**
   - Primary: `get_financials` for recent financial health context.
   - If unavailable or credits exhausted: `get_yahoo_finance_data` with `action='quote_summary'`.

6. **Options positioning (always check)**
   - Use `get_yahoo_finance_data` with `action='options'`.
   - Assess unusual OI/skew around likely catalyst dates where possible.

## Fallback Policy

Apply this policy for the remainder of the run as soon as paid endpoints fail with credit/rate issues:

- If any Financial Datasets call fails (especially 402/insufficient credits):
  - Switch price/news to `get_alpaca_market_data`
  - Switch fundamentals/options to `get_yahoo_finance_data`
  - Continue report generation; do not fail the brief

## Scoring Rules

- `conviction` must be 0-100 and evidence-driven.
- Mark `high_conviction=true` only when conviction is **>= 70**.
- Use `direction='skip'` when catalysts are weak, timing is uncertain, or data quality is insufficient.

## Output Requirements

1. Provide a concise narrative summary first.
2. End with a fenced JSON block in this exact shape:

```json
{
  "week_of": "2026-05-18",
  "signals": [
    {
      "ticker": "VRTX",
      "direction": "long",
      "conviction": 78,
      "thesis": "Clear and concise thesis grounded in observed catalysts and data.",
      "catalysts": [
        { "date": "2026-05-20", "event": "Catalyst description" }
      ],
      "risks": [
        "One clear invalidation risk tied to timing/data/financing"
      ],
      "high_conviction": true
    }
  ]
}
```

## Quality Bar

- No invented dates or unverified catalyst claims.
- Keep thesis tight and decision-oriented.
- Include at least one key risk or invalidation condition per ticker.
- `signals[*].risks` should be a non-empty string array whenever possible.
