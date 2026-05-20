import { Client } from 'pg';

export type WatchlistRow = {
  ticker: string;
  company_name: string | null;
  expected_catalyst: string | null;
  discovery_source: string | null;
};

export async function fetchActiveWatchlist(): Promise<WatchlistRow[]> {
  const databaseUrl = process.env.DATABASE_URL;
  if (!databaseUrl) return [];
  const client = new Client({ connectionString: databaseUrl });
  await client.connect();
  try {
    const result = await client.query<WatchlistRow>(
      `SELECT ticker, company_name, expected_catalyst, discovery_source
       FROM weekly_watchlist
       WHERE active = TRUE`,
    );
    return result.rows.map((r) => ({
      ticker: (r.ticker || '').toUpperCase(),
      company_name: r.company_name,
      expected_catalyst: r.expected_catalyst,
      discovery_source: r.discovery_source,
    }));
  } finally {
    await client.end();
  }
}

export async function sendDiscordMessage(
  content: string,
  username = 'Weekly Discovery',
): Promise<void> {
  const webhook = (process.env.DISCORD_WEBHOOK_URL || '').trim();
  if (!webhook) {
    throw new Error('DISCORD_WEBHOOK_URL is required (set it in dexter/.env)');
  }
  const response = await fetch(webhook, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      username,
      content,
    }),
  });
  if (!response.ok) {
    throw new Error(`Discord webhook failed: ${response.status} ${response.statusText}`);
  }
}

export function chunkMessage(prefix: string, body: string, maxChars = 1900): string[] {
  const result: string[] = [];
  const lines = body.split('\n');
  let current = prefix;

  for (const line of lines) {
    if ((current + '\n' + line).length > maxChars) {
      result.push(current);
      current = line;
    } else {
      current = current ? `${current}\n${line}` : line;
    }
  }
  if (current) result.push(current);
  return result;
}

export function extractJsonPayload<T>(text: string, validate: (payload: T) => boolean): T {
  const match = text.match(/```json\s*([\s\S]*?)\s*```/g);
  if (!match || match.length === 0) {
    throw new Error('No JSON code block found in agent response');
  }
  const last = match[match.length - 1];
  const jsonMatch = last.match(/```json\s*([\s\S]*?)\s*```/);
  if (!jsonMatch) {
    throw new Error('Failed to parse JSON code block');
  }

  const payload = JSON.parse(jsonMatch[1]) as T;
  if (!validate(payload)) {
    throw new Error('Invalid JSON payload shape');
  }
  return payload;
}
