---
name: biotech-daily-pulse
description: Produce a free daily biotech delta report versus Monday's thesis using Alpaca, Yahoo, Exa, and ClinicalTrials.gov.
---

# Biotech Daily Pulse Skill

You are generating a pre-market daily pulse for biotech names already covered by Monday's weekly brief.

## Goal

Produce a concise change-tracking report that compares **today** versus the most recent Monday thesis.

## Required Inputs (provided in prompt context)

- `pulse_date` (YYYY-MM-DD)
- `ref_week_of` (Monday YYYY-MM-DD or null)
- `tickers` (array)
- `monday_brief_by_ticker` object with prior direction, conviction, thesis, and catalysts

## Mandatory Per-Ticker Workflow

For each ticker in `tickers`, execute these steps:

1. Read that ticker's baseline in `monday_brief_by_ticker`.
2. Pull fresh market action using `get_alpaca_market_data`:
   - action `latest_quote`
   - action `bars` (at least 1d context)
3. Pull fresh news:
   - `get_alpaca_market_data` with action `news` (24h window if available)
   - `web_search` for latest biotech-specific headlines.
4. Pull options tone:
   - `get_yahoo_finance_data` with action `options` (nearest expiries).
5. If an NCT identifier exists in baseline context, check ClinicalTrials.gov with `web_fetch`.
6. Assign exactly one status:
   - `on_track`
   - `accelerating`
   - `breakdown`
   - `catalyst_today`
   - `quiet`
7. Write a 2-3 line note focused on what changed since Monday.

## Style Rules

- Keep output actionable and short.
- Prioritize catalyst timing, directional confirmation, and unusual option/volume signals.
- If data is missing, say so explicitly instead of guessing.

## Output Format (strict)

1. Human-readable section with:
   - title line
   - catalysts today
   - catalysts this week
   - since-Monday bullets
   - overnight news bullets
   - one-line action
2. End with a fenced `json` block containing:

```json
{
  "pulse_date": "YYYY-MM-DD",
  "ref_week_of": "YYYY-MM-DD or null",
  "tickers": [
    {
      "ticker": "VRTX",
      "status": "on_track",
      "change_pct": 0.0,
      "note": "2-3 lines of delta-focused commentary"
    }
  ],
  "action_items": [
    "Short actionable checklist item"
  ]
}
```

### Validation constraints

- `tickers[*].status` must be one of: `on_track`, `accelerating`, `breakdown`, `catalyst_today`, `quiet`.
- `change_pct` must be numeric.
- Include every ticker from input universe exactly once.
