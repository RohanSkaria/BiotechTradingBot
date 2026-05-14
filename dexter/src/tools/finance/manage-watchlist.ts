import { DynamicStructuredTool } from '@langchain/core/tools';
import { z } from 'zod';
import { Client } from 'pg';
import { formatToolResult } from '../types.js';

const DEXTER_BRIEF_SOURCE = 'dexter_brief';
const DEFAULT_STALE_DAYS = 14;

export const MANAGE_WATCHLIST_DESCRIPTION = `
Manage the biotech weekly watchlist stored in Neon. Supports three actions:

- expire_stale: Soft-mark Dexter-discovered entries older than a configurable number of days as inactive. Only touches rows with discovery_source='dexter_brief'; manual and other-source entries are never modified.
- list_active: Read all currently active watchlist entries with their discovery sources.
- add: Upsert a ticker into the watchlist. If the ticker already exists, refresh updated_at and expected_catalyst; do not change the original discovery_source. Use this to seed pioneering biotech candidates for next week's brief.

## When to Use
- Run expire_stale at the very start of a weekly brief to keep the universe focused.
- Run list_active right after expire_stale to see what's already tracked before discovering new candidates.
- Run add for each new ticker with a verifiable near-term catalyst.

## Safety
- Never deletes rows. Removal is always soft (active=FALSE).
- expire_stale never touches rows whose discovery_source is not 'dexter_brief'.
`.trim();

const ManageWatchlistInputSchema = z.object({
  action: z
    .enum(['expire_stale', 'list_active', 'add'])
    .describe('Which watchlist management action to perform.'),
  stale_days: z
    .number()
    .int()
    .min(1)
    .max(60)
    .default(DEFAULT_STALE_DAYS)
    .describe("Days threshold for expire_stale. Defaults to 14."),
  ticker: z
    .string()
    .optional()
    .describe("Ticker symbol. Required when action='add'."),
  company_name: z
    .string()
    .optional()
    .describe("Company name. Optional for action='add'."),
  expected_catalyst: z
    .string()
    .optional()
    .describe("Brief description of the catalyst and timing (e.g., 'PDUFA decision for ABC-123 on 2026-05-22'). Optional for action='add'."),
  market_cap_tier: z
    .enum(['small', 'mid', 'large'])
    .optional()
    .describe("Market cap tier. Optional for action='add'."),
  priority: z
    .enum(['low', 'medium', 'high'])
    .optional()
    .describe("Priority. Optional for action='add'."),
});

function getDatabaseUrl(): string {
  const url = process.env.DATABASE_URL;
  if (!url) {
    throw new Error('DATABASE_URL is required for manage_watchlist');
  }
  return url;
}

async function withClient<T>(fn: (client: Client) => Promise<T>): Promise<T> {
  const client = new Client({ connectionString: getDatabaseUrl() });
  await client.connect();
  try {
    return await fn(client);
  } finally {
    await client.end();
  }
}

async function expireStale(staleDays: number): Promise<unknown> {
  return withClient(async (client) => {
    const result = await client.query<{ ticker: string }>(
      `
      UPDATE weekly_watchlist
      SET active = FALSE,
          updated_at = CURRENT_TIMESTAMP
      WHERE discovery_source = $1
        AND active = TRUE
        AND updated_at < CURRENT_TIMESTAMP - ($2 || ' days')::interval
      RETURNING ticker
      `,
      [DEXTER_BRIEF_SOURCE, String(staleDays)],
    );
    return {
      expired_count: result.rowCount ?? 0,
      expired_tickers: result.rows.map((r) => r.ticker),
      stale_days: staleDays,
    };
  });
}

async function listActive(): Promise<unknown> {
  return withClient(async (client) => {
    const result = await client.query<{
      ticker: string;
      company_name: string | null;
      expected_catalyst: string | null;
      market_cap_tier: string | null;
      priority: string | null;
      discovery_source: string | null;
      updated_at: Date;
    }>(
      `
      SELECT ticker, company_name, expected_catalyst, market_cap_tier,
             priority, discovery_source, updated_at
      FROM weekly_watchlist
      WHERE active = TRUE
      ORDER BY priority DESC, ticker
      `,
    );
    return {
      count: result.rowCount ?? 0,
      tickers: result.rows.map((r) => ({
        ticker: r.ticker,
        company_name: r.company_name,
        expected_catalyst: r.expected_catalyst,
        market_cap_tier: r.market_cap_tier,
        priority: r.priority,
        discovery_source: r.discovery_source,
        updated_at: r.updated_at?.toISOString(),
      })),
    };
  });
}

async function addTicker(args: {
  ticker: string;
  company_name?: string;
  expected_catalyst?: string;
  market_cap_tier?: string;
  priority?: string;
}): Promise<unknown> {
  const ticker = args.ticker.trim().toUpperCase();
  if (!ticker) {
    throw new Error("ticker is required when action='add'");
  }

  return withClient(async (client) => {
    const existing = await client.query<{ cik: string; discovery_source: string | null }>(
      `SELECT cik, discovery_source FROM weekly_watchlist WHERE UPPER(ticker) = $1 LIMIT 1`,
      [ticker],
    );

    if ((existing.rowCount ?? 0) > 0) {
      const row = existing.rows[0];
      await client.query(
        `
        UPDATE weekly_watchlist
        SET company_name = COALESCE($1, company_name),
            expected_catalyst = COALESCE($2, expected_catalyst),
            market_cap_tier = COALESCE($3, market_cap_tier),
            priority = COALESCE($4, priority),
            active = TRUE,
            updated_at = CURRENT_TIMESTAMP
        WHERE cik = $5
        `,
        [
          args.company_name ?? null,
          args.expected_catalyst ?? null,
          args.market_cap_tier ?? null,
          args.priority ?? null,
          row.cik,
        ],
      );
      return {
        action: 'updated',
        ticker,
        original_discovery_source: row.discovery_source,
      };
    }

    await client.query(
      `
      INSERT INTO weekly_watchlist
        (cik, ticker, company_name, expected_catalyst, market_cap_tier,
         priority, discovery_source, active, created_at, updated_at)
      VALUES ($1, $2, $3, $4, $5, $6, $7, TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
      `,
      [
        ticker,
        ticker,
        args.company_name ?? null,
        args.expected_catalyst ?? null,
        args.market_cap_tier ?? null,
        args.priority ?? 'medium',
        DEXTER_BRIEF_SOURCE,
      ],
    );
    return {
      action: 'inserted',
      ticker,
      discovery_source: DEXTER_BRIEF_SOURCE,
    };
  });
}

export const manageWatchlistTool = new DynamicStructuredTool({
  name: 'manage_watchlist',
  description:
    'Manage the biotech weekly watchlist in Neon: expire stale dexter-discovered entries, list active entries, or add/refresh a ticker. Soft-delete only.',
  schema: ManageWatchlistInputSchema,
  func: async (input) => {
    try {
      let data: unknown;
      if (input.action === 'expire_stale') {
        data = await expireStale(input.stale_days);
      } else if (input.action === 'list_active') {
        data = await listActive();
      } else {
        data = await addTicker({
          ticker: input.ticker ?? '',
          company_name: input.company_name,
          expected_catalyst: input.expected_catalyst,
          market_cap_tier: input.market_cap_tier,
          priority: input.priority,
        });
      }
      return formatToolResult(data, []);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      return formatToolResult({ error: message }, []);
    }
  },
});
