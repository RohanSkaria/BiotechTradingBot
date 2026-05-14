# Weekly Brief Evaluation Runbook

Use this runbook after running the Monday weekly brief for 2-3 weeks.

## Goal

Decide whether the weekly brief quality is high enough to move to Phase 2 (trade execution wiring).

## Data source

- Table: `weekly_briefings`
- Fields used: `week_of`, `ticker`, `direction`, `conviction`, `high_conviction`, `thesis`, `created_at`

## Weekly checklist

1. Confirm Monday run posted to Discord and inserted rows into `weekly_briefings`.
2. For each `high_conviction = TRUE` row, manually track the ticker's weekly move and whether the cited catalyst happened on time.
3. Grade each signal:
   - `hit` (direction aligned with outcome)
   - `neutral` (no meaningful move / ambiguous catalyst)
   - `miss` (direction contradicted by outcome)
4. Review false positives where conviction >= 70 but catalyst quality was weak.
5. Review false negatives where skipped tickers had major moves.

## SQL queries

```sql
-- Weekly output volume and conviction profile
SELECT
  week_of,
  COUNT(*) AS total_signals,
  SUM(CASE WHEN high_conviction THEN 1 ELSE 0 END) AS high_conviction_signals,
  ROUND(AVG(conviction)::numeric, 2) AS avg_conviction
FROM weekly_briefings
GROUP BY week_of
ORDER BY week_of DESC;
```

```sql
-- Most recent week's detailed output
SELECT
  week_of, ticker, direction, conviction, high_conviction, created_at
FROM weekly_briefings
WHERE week_of = (SELECT MAX(week_of) FROM weekly_briefings)
ORDER BY conviction DESC, ticker;
```

## Promotion criteria for Phase 2

After at least 2 full weeks, proceed only if:

- At least 60% of high-conviction calls are graded `hit` or `neutral`
- No major risk-control misses in thesis quality (e.g., fabricated catalyst timing)
- Discord output is consistently clear and actionable

If criteria are not met, continue Phase 1 and tighten skill instructions before enabling trade execution.
