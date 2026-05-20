#!/usr/bin/env bun
import { config } from 'dotenv';
import { Client } from 'pg';
import { runAgentForMessage } from '../src/gateway/agent-runner.js';
import {
  chunkMessage,
  extractJsonPayload,
  fetchActiveWatchlist,
  sendDiscordMessage,
  withMention,
  type WatchlistRow,
} from './lib/brief-utils.js';

config({ quiet: true });

type WeeklySignal = {
  ticker: string;
  direction: 'long' | 'short' | 'skip';
  conviction: number;
  thesis: string;
  catalysts: Array<{ date?: string; event?: string }>;
  risks?: string[];
  high_conviction: boolean;
};

type WeeklyBriefPayload = {
  week_of: string;
  signals: WeeklySignal[];
};

function getMondayIso(date = new Date()): string {
  const d = new Date(date);
  const day = d.getUTCDay();
  const diff = (day + 6) % 7;
  d.setUTCDate(d.getUTCDate() - diff);
  return d.toISOString().slice(0, 10);
}

function getWeekLabel(weekOf: string): string {
  const d = new Date(`${weekOf}T12:00:00Z`);
  return d.toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    timeZone: 'America/New_York',
  });
}


export function buildDiscordMessages(
  inputTickers: string[],
  payload: WeeklyBriefPayload,
  fullReport: string,
  newlyDiscovered: WatchlistRow[],
): string[] {
  const sortedSignals = [...payload.signals].sort((a, b) => b.conviction - a.conviction);
  const high = sortedSignals.filter((s) => s.high_conviction);
  const seen = new Set(sortedSignals.map((s) => s.ticker.toUpperCase()));
  const quiet = inputTickers.filter((t) => !seen.has(t.toUpperCase()));

  const newlyDiscoveredTickers = new Set(newlyDiscovered.map((r) => r.ticker.toUpperCase()));
  const newlyDiscoveredUnanalyzed = newlyDiscovered.filter(
    (r) => !seen.has(r.ticker.toUpperCase()),
  );

  const summaryLines: string[] = [];
  summaryLines.push(`Biotech Weekly Brief | Week of ${getWeekLabel(payload.week_of)}`);
  const inputCount = inputTickers.length;
  const discoveryCount = newlyDiscovered.length;
  const scanLine = discoveryCount > 0
    ? `Scanned ${inputCount + discoveryCount} tickers (${inputCount} input + ${discoveryCount} newly discovered). ${high.length} high-conviction setup(s) this week.`
    : `Scanned ${inputCount} tickers. ${high.length} high-conviction setup(s) this week.`;
  summaryLines.push(scanLine);
  if (high.length > 0) {
    summaryLines.push(`Top pick: ${high[0].ticker} (${high[0].conviction}% conviction)`);
  }
  summaryLines.push('');

  if (high.length === 0) {
    summaryLines.push('No high-conviction setups this week.');
  } else {
    for (const signal of high) {
      const emoji = signal.direction === 'short' ? '🔴' : '🟡';
      const newBadge = newlyDiscoveredTickers.has(signal.ticker.toUpperCase()) ? ' 🆕' : '';
      const catalystStr = signal.catalysts.slice(0, 2).map((c) => c.event || 'Catalyst').join(' | ');
      summaryLines.push(
        `${emoji} ${signal.ticker}${newBadge} - ${signal.direction.toUpperCase()} (${signal.conviction}% conviction)${
          catalystStr ? ` | ${catalystStr}` : ''
        }`,
      );
    }
  }

  if (newlyDiscoveredUnanalyzed.length > 0) {
    summaryLines.push('');
    summaryLines.push('🆕 Newly added to watchlist (full analysis next week):');
    for (const row of newlyDiscoveredUnanalyzed) {
      const label = row.company_name ? `${row.ticker} (${row.company_name})` : row.ticker;
      const catalyst = row.expected_catalyst ? ` | ${row.expected_catalyst}` : '';
      summaryLines.push(`- ${label}${catalyst}`);
    }
  }

  if (quiet.length > 0) {
    summaryLines.push('');
    summaryLines.push(`Quiet: ${quiet.join(', ')}`);
  }

  const messages: string[] = [summaryLines.join('\n')];

  for (const signal of high) {
    const catalystLines =
      signal.catalysts.length === 0
        ? '- No dated catalyst extracted'
        : signal.catalysts
            .map((c) => `- ${c.date || 'TBD'}: ${c.event || 'Unspecified catalyst'}`)
            .join('\n');

    const detail = [
      `**${signal.ticker} | ${signal.direction.toUpperCase()} | ${signal.conviction}% conviction**`,
      '',
      '**Catalysts**',
      catalystLines,
      '',
      '**Thesis**',
      signal.thesis,
      '',
      '**Key Risks**',
      ...(signal.risks && signal.risks.length > 0
        ? signal.risks.map((risk) => `- ${risk}`)
        : ['- Thesis invalid if catalyst timing slips, data readout quality disappoints, or financing overhang increases.']),
    ].join('\n');

    const chunks = chunkMessage('', detail);
    if (chunks.length === 1) {
      messages.push(chunks[0]);
    } else {
      for (let i = 0; i < chunks.length; i += 1) {
        messages.push(`${chunks[i]}\n\n(${i + 1}/${chunks.length})`);
      }
    }
  }

  if (high.length === 0) {
    const compressed = fullReport.length > 1800 ? `${fullReport.slice(0, 1750)}...` : fullReport;
    messages.push(`Context:\n${compressed}`);
  }

  return messages;
}

async function postDiscordBrief(
  inputTickers: string[],
  payload: WeeklyBriefPayload,
  fullReport: string,
  newlyDiscovered: WatchlistRow[],
): Promise<void> {
  const messages = buildDiscordMessages(inputTickers, payload, fullReport, newlyDiscovered);
  if (messages.length > 0) {
    messages[0] = withMention(messages[0]);
  }
  for (const message of messages) {
    await sendDiscordMessage(message, 'Weekly Discovery');
  }
}

async function persistWeeklyBrief(payload: WeeklyBriefPayload, fullReport: string): Promise<void> {
  const databaseUrl = process.env.DATABASE_URL;
  if (!databaseUrl) {
    throw new Error('DATABASE_URL is required for weekly brief persistence');
  }

  const client = new Client({ connectionString: databaseUrl });
  await client.connect();
  try {
    for (const signal of payload.signals) {
      await client.query(
        `
        INSERT INTO weekly_briefings
          (week_of, ticker, direction, conviction, thesis, catalysts, high_conviction, full_report, discord_posted)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, TRUE)
        ON CONFLICT (week_of, ticker)
        DO UPDATE SET
          direction = EXCLUDED.direction,
          conviction = EXCLUDED.conviction,
          thesis = EXCLUDED.thesis,
          catalysts = EXCLUDED.catalysts,
          high_conviction = EXCLUDED.high_conviction,
          full_report = EXCLUDED.full_report,
          discord_posted = TRUE
      `,
        [
          payload.week_of,
          signal.ticker.toUpperCase(),
          signal.direction,
          signal.conviction,
          signal.thesis,
          JSON.stringify(signal.catalysts || []),
          Boolean(signal.high_conviction),
          fullReport,
        ],
      );
    }
  } finally {
    await client.end();
  }
}

async function main(): Promise<void> {
  const tickers = process.argv.slice(2).map((t) => t.trim().toUpperCase()).filter(Boolean);
  if (tickers.length === 0) {
    throw new Error('Please pass one or more tickers. Example: bun run scripts/weekly-brief.ts LLY VRTX');
  }

  const weekOf = getMondayIso();
  const query = [
    `Produce a biotech weekly brief for week_of=${weekOf}.`,
    `Ticker universe: ${tickers.join(', ')}`,
    'Use the "biotech-weekly-brief" skill.',
    'End with a fenced ```json block using the required schema.',
  ].join('\n');

  const watchlistBefore = await fetchActiveWatchlist();
  const beforeTickers = new Set(watchlistBefore.map((r) => r.ticker));

  const answer = await runAgentForMessage({
    sessionKey: `weekly-brief:${weekOf}`,
    query,
    model: process.env.WEEKLY_BRIEF_MODEL || 'claude-sonnet-4-5',
    modelProvider: 'anthropic',
    maxIterations: 25,
    isolatedSession: true,
  });

  const watchlistAfter = await fetchActiveWatchlist();
  const newlyDiscovered = watchlistAfter.filter((r) => !beforeTickers.has(r.ticker));

  const payload = extractJsonPayload<WeeklyBriefPayload>(
    answer,
    (candidate) => Boolean(candidate.week_of) && Array.isArray(candidate.signals),
  );
  await persistWeeklyBrief(payload, answer);
  await postDiscordBrief(tickers, payload, answer, newlyDiscovered);

  console.log(
    JSON.stringify(
      {
        ok: true,
        week_of: payload.week_of,
        signals: payload.signals.length,
        newly_discovered: newlyDiscovered.map((r) => r.ticker),
      },
      null,
      2,
    ),
  );
}

if (import.meta.main) {
  main().catch((error) => {
    const message = error instanceof Error ? error.message : String(error);
    console.error(message);
    process.exit(1);
  });
}
