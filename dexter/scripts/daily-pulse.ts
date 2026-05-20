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
} from './lib/brief-utils.js';

config({ quiet: true });

type MondayBriefRow = {
  ticker: string;
  direction: 'long' | 'short' | 'skip' | null;
  conviction: number | null;
  thesis: string | null;
  catalysts: Array<{ date?: string; event?: string }>;
};

type DailyPulseTicker = {
  ticker: string;
  status: 'on_track' | 'accelerating' | 'breakdown' | 'catalyst_today' | 'quiet';
  change_pct: number;
  note: string;
};

type PulseCatalyst = {
  ticker: string;
  event: string;
  days_out?: number;
};

type DailyPulsePayload = {
  pulse_date: string;
  ref_week_of: string | null;
  catalysts_today?: PulseCatalyst[];
  catalysts_this_week?: PulseCatalyst[];
  tickers: DailyPulseTicker[];
  action_items: string[];
};

function getTodayIso(date = new Date()): string {
  return date.toISOString().slice(0, 10);
}

function getPulseLabel(pulseDate: string): string {
  const d = new Date(`${pulseDate}T12:00:00Z`);
  return d.toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    timeZone: 'America/New_York',
  });
}

function ensureGoogleKeyAlias(): void {
  const google = process.env.GOOGLE_API_KEY?.trim();
  const gemini = process.env.GEMINI_API_KEY?.trim();
  if ((!google || google.startsWith('your-')) && gemini) {
    process.env.GOOGLE_API_KEY = gemini;
  }
}

function stripTrailingJsonBlock(report: string): string {
  return report.replace(/\n?```json[\s\S]*?```[\s]*$/i, '').trim();
}

function validateDailyPayload(
  candidate: DailyPulsePayload,
  tickers: string[],
  pulseDate: string,
): boolean {
  if (!candidate || candidate.pulse_date !== pulseDate || !Array.isArray(candidate.tickers)) {
    return false;
  }
  const expected = new Set(tickers.map((t) => t.toUpperCase()));
  const seen = new Set<string>();
  const validStatuses = new Set([
    'on_track',
    'accelerating',
    'breakdown',
    'catalyst_today',
    'quiet',
  ]);
  for (const row of candidate.tickers) {
    const ticker = (row.ticker || '').toUpperCase();
    if (!expected.has(ticker)) return false;
    if (seen.has(ticker)) return false;
    if (!validStatuses.has(row.status)) return false;
    if (typeof row.change_pct !== 'number' || Number.isNaN(row.change_pct)) return false;
    seen.add(ticker);
  }
  const catalystArrays = [candidate.catalysts_today, candidate.catalysts_this_week];
  for (const items of catalystArrays) {
    if (items === undefined) continue;
    if (!Array.isArray(items)) return false;
    for (const item of items) {
      if (!item || typeof item.ticker !== 'string' || typeof item.event !== 'string') return false;
      if (item.days_out !== undefined && typeof item.days_out !== 'number') return false;
    }
  }
  return seen.size === expected.size;
}

async function ensureDailyPulseTable(client: Client): Promise<void> {
  await client.query(`
    CREATE TABLE IF NOT EXISTS daily_pulses (
      id SERIAL PRIMARY KEY,
      pulse_date DATE NOT NULL,
      ref_week_of DATE,
      tickers_covered TEXT[],
      summary TEXT,
      full_report TEXT,
      discord_posted BOOLEAN DEFAULT FALSE,
      created_at TIMESTAMP DEFAULT NOW(),
      UNIQUE(pulse_date)
    );
  `);
}

async function fetchMondayContext(
  client: Client,
  tickers: string[],
  pulseDate: string,
): Promise<{ refWeekOf: string | null; rows: MondayBriefRow[] }> {
  const refResult = await client.query<{ ref_week_of: string | null }>(
    `
      SELECT MAX(week_of)::text AS ref_week_of
      FROM weekly_briefings
      WHERE week_of <= $1::date
    `,
    [pulseDate],
  );
  const refWeekOf = refResult.rows[0]?.ref_week_of || null;
  if (!refWeekOf) return { refWeekOf: null, rows: [] };

  const rowsResult = await client.query<MondayBriefRow>(
    `
      SELECT ticker, direction, conviction, thesis, catalysts
      FROM weekly_briefings
      WHERE week_of = $1::date
        AND ticker = ANY($2::text[])
    `,
    [refWeekOf, tickers],
  );
  return { refWeekOf, rows: rowsResult.rows };
}

function buildQuery(
  pulseDate: string,
  tickers: string[],
  refWeekOf: string | null,
  mondayRows: MondayBriefRow[],
): string {
  const mondayMap = Object.fromEntries(
    mondayRows.map((row) => [
      row.ticker.toUpperCase(),
      {
        direction: row.direction,
        conviction: row.conviction,
        thesis: row.thesis,
        catalysts: row.catalysts || [],
      },
    ]),
  );

  return [
    `Produce a biotech daily pulse for pulse_date=${pulseDate}.`,
    `Ticker universe: ${tickers.join(', ')}`,
    `ref_week_of=${refWeekOf ?? 'null'}`,
    'Use the "biotech-daily-pulse" skill.',
    'End with a fenced ```json block using the required schema.',
    '',
    'Context payload (from Neon weekly_briefings):',
    '```json',
    JSON.stringify(
      {
        pulse_date: pulseDate,
        ref_week_of: refWeekOf,
        tickers,
        monday_brief_by_ticker: mondayMap,
      },
      null,
      2,
    ),
    '```',
  ].join('\n');
}

function buildFallbackPayload(
  pulseDate: string,
  refWeekOf: string | null,
  tickers: string[],
): DailyPulsePayload {
  return {
    pulse_date: pulseDate,
    ref_week_of: refWeekOf,
    tickers: tickers.map((ticker) => ({
      ticker,
      status: 'quiet',
      change_pct: 0,
      note: refWeekOf
        ? 'Baseline fallback: Gemini tool-calling response was empty, marked quiet pending next run.'
        : 'No reference Monday brief available yet; using today as baseline.',
    })),
    action_items: [
      refWeekOf
        ? 'Re-run pulse manually after market open if a deeper catalyst delta is needed.'
        : 'Run Monday weekly brief first, then daily pulse deltas will be anchored.',
    ],
  };
}

const STATUS_PRIORITY: Record<DailyPulseTicker['status'], number> = {
  catalyst_today: 4,
  breakdown: 3,
  accelerating: 2,
  on_track: 1,
  quiet: 0,
};

function formatChangePct(changePct: number): string {
  return `${changePct >= 0 ? '+' : ''}${changePct.toFixed(1)}%`;
}

function buildStatusBuckets(tickers: DailyPulseTicker[]): string[] {
  const order: Array<DailyPulseTicker['status']> = [
    'catalyst_today',
    'accelerating',
    'breakdown',
    'on_track',
    'quiet',
  ];
  const lines: string[] = [];
  for (const status of order) {
    const names = tickers
      .filter((t) => t.status === status)
      .map((t) => t.ticker.toUpperCase());
    if (names.length > 0) {
      lines.push(`- ${status}: ${names.join(', ')}`);
    }
  }
  return lines;
}

function buildDeterministicMessage(
  pulseDate: string,
  payload: DailyPulsePayload,
): string {
  const catalystsToday = payload.catalysts_today || [];
  const catalystsThisWeek = payload.catalysts_this_week || [];
  const actionItems = payload.action_items || [];
  const tickers = payload.tickers || [];
  const allQuiet = tickers.length > 0 && tickers.every((t) => t.status === 'quiet');

  if (catalystsToday.length === 0 && allQuiet && actionItems.length === 0) {
    return [
      `Biotech Daily Pulse | ${getPulseLabel(pulseDate)}`,
      `All quiet vs Monday thesis. ${tickers.length} tickers monitored. No catalysts today.`,
    ].join('\n');
  }

  const lines: string[] = [];
  lines.push(`Biotech Daily Pulse | ${getPulseLabel(pulseDate)}`);
  lines.push(
    payload.ref_week_of
      ? `Anchored to week of ${payload.ref_week_of} | ${tickers.length} tickers covered`
      : `No reference week available | ${tickers.length} tickers covered`,
  );
  lines.push('');

  const ranked = [...tickers]
    .filter((t) => t.status !== 'quiet')
    .sort(
      (a, b) =>
        STATUS_PRIORITY[b.status] - STATUS_PRIORITY[a.status] ||
        Math.abs(b.change_pct) - Math.abs(a.change_pct),
    );
  if (ranked.length >= 2) {
    lines.push('Top 3 to watch today');
    for (const item of ranked.slice(0, 3)) {
      lines.push(`- ${item.ticker} ${item.status} ${formatChangePct(item.change_pct)} - ${item.note}`);
    }
    lines.push('');
  }

  if (catalystsToday.length > 0) {
    lines.push('Catalysts today');
    for (const catalyst of catalystsToday) {
      lines.push(`- ${catalyst.ticker} - ${catalyst.event}`);
    }
    lines.push('');
  }

  if (catalystsThisWeek.length > 0) {
    lines.push('Catalysts this week');
    for (const catalyst of catalystsThisWeek) {
      const suffix =
        typeof catalyst.days_out === 'number' ? ` (${catalyst.days_out} trading days)` : '';
      lines.push(`- ${catalyst.ticker} - ${catalyst.event}${suffix}`);
    }
    lines.push('');
  }

  lines.push('Status');
  lines.push(...buildStatusBuckets(tickers));
  lines.push('');

  lines.push('Notes');
  const noteRows = ranked.length > 0 ? ranked.slice(0, 3) : tickers.slice(0, 3);
  for (const item of noteRows) {
    lines.push(`- ${item.ticker}: ${item.note}`);
  }
  lines.push('');

  if (actionItems.length > 0) {
    lines.push('Action');
    for (const action of actionItems.slice(0, 3)) {
      lines.push(`- ${action}`);
    }
  } else {
    lines.push('Action');
    lines.push('- Monitor catalysts and thesis drift into the open.');
  }

  return lines.join('\n').trim();
}

export function buildDiscordMessages(
  pulseDate: string,
  payload: DailyPulsePayload,
  fullReport: string,
): string[] {
  const renderMode = (process.env.DAILY_PULSE_RENDER_MODE || '').trim().toLowerCase();
  const summary =
    renderMode === 'narrative'
      ? (() => {
          const header = `Biotech Daily Pulse | ${getPulseLabel(pulseDate)}`;
          const body = stripTrailingJsonBlock(fullReport);
          return body
            ? `${header}\n\n${body}`
            : `${header}\n\nNo narrative generated. See JSON payload for structured status.`;
        })()
      : buildDeterministicMessage(pulseDate, payload);

  const chunks = chunkMessage('', summary, 1900);
  return chunks.length > 0 ? chunks : [summary];
}

async function persistDailyPulse(
  client: Client,
  payload: DailyPulsePayload,
  fullReport: string,
): Promise<void> {
  const summaryLine = fullReport.split('\n').find((line) => line.trim().length > 0) || '';
  const tickersCovered = payload.tickers.map((t) => t.ticker.toUpperCase());

  await client.query(
    `
      INSERT INTO daily_pulses
        (pulse_date, ref_week_of, tickers_covered, summary, full_report, discord_posted)
      VALUES ($1::date, $2::date, $3::text[], $4, $5, TRUE)
      ON CONFLICT (pulse_date)
      DO UPDATE SET
        ref_week_of = EXCLUDED.ref_week_of,
        tickers_covered = EXCLUDED.tickers_covered,
        summary = EXCLUDED.summary,
        full_report = EXCLUDED.full_report,
        discord_posted = TRUE
    `,
    [
      payload.pulse_date,
      payload.ref_week_of,
      tickersCovered,
      summaryLine.slice(0, 500),
      fullReport,
    ],
  );
}

async function postDiscordPulse(messages: string[]): Promise<void> {
  const prepared = [...messages];
  if (prepared.length > 0) {
    prepared[0] = withMention(prepared[0]);
  }
  for (const message of prepared) {
    await sendDiscordMessage(message, 'Daily Pulse');
  }
}

async function main(): Promise<void> {
  ensureGoogleKeyAlias();
  const pulseDate = getTodayIso();
  const watchlistRows = await fetchActiveWatchlist();
  const tickers = Array.from(new Set(watchlistRows.map((r) => r.ticker.toUpperCase()))).sort();
  if (tickers.length === 0) {
    throw new Error('No active weekly_watchlist tickers found for daily pulse.');
  }

  const databaseUrl = process.env.DATABASE_URL;
  if (!databaseUrl) {
    throw new Error('DATABASE_URL is required for daily pulse persistence');
  }

  const client = new Client({ connectionString: databaseUrl });
  await client.connect();
  try {
    await ensureDailyPulseTable(client);
    const { refWeekOf, rows } = await fetchMondayContext(client, tickers, pulseDate);
    const query = buildQuery(pulseDate, tickers, refWeekOf, rows);

    let answer = await runAgentForMessage({
      sessionKey: `daily-pulse:${pulseDate}`,
      query,
      model: process.env.DAILY_PULSE_MODEL || 'gemini-2.5-flash',
      modelProvider: 'google_genai',
      maxIterations: 15,
      isolatedSession: true,
    });

    let payload: DailyPulsePayload;
    try {
      payload = extractJsonPayload<DailyPulsePayload>(answer, (candidate) =>
        validateDailyPayload(candidate, tickers, pulseDate),
      );
    } catch {
      payload = buildFallbackPayload(pulseDate, refWeekOf, tickers);
      answer = [
        `Biotech Daily Pulse | ${getPulseLabel(pulseDate)}`,
        '',
        refWeekOf
          ? `Reference week: ${refWeekOf}. Fallback mode used because Gemini returned an empty or invalid tool-calling payload.`
          : 'No reference week available; treating today as baseline.',
        '',
        'Since Monday:',
        ...payload.tickers.map((row) => `- ${row.ticker} ${row.change_pct}% | ${row.status}`),
        '',
        `Action: ${payload.action_items[0] || 'Monitor catalysts and rerun if needed.'}`,
        '',
        '```json',
        JSON.stringify(payload, null, 2),
        '```',
      ].join('\n');
    }

    await persistDailyPulse(client, payload, answer);
    const messages = buildDiscordMessages(pulseDate, payload, answer);
    await postDiscordPulse(messages);

    console.log(
      JSON.stringify(
        {
          ok: true,
          pulse_date: payload.pulse_date,
          ref_week_of: payload.ref_week_of,
          tickers: payload.tickers.length,
        },
        null,
        2,
      ),
    );
  } finally {
    await client.end();
  }
}

if (import.meta.main) {
  main().catch((error) => {
    const message = error instanceof Error ? error.message : String(error);
    console.error(message);
    process.exit(1);
  });
}
