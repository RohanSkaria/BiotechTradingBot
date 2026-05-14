import { DynamicStructuredTool } from '@langchain/core/tools';
import { z } from 'zod';
import { formatToolResult } from '../types.js';

const ALPACA_DATA_BASE_URL = 'https://data.alpaca.markets';

function getAlpacaHeaders(): Record<string, string> {
  const key = process.env.ALPACA_KEY || '';
  const secret = process.env.ALPACA_SECRET || '';
  if (!key || !secret) {
    throw new Error('ALPACA_KEY and ALPACA_SECRET must be configured to use get_alpaca_market_data');
  }
  return {
    'APCA-API-KEY-ID': key,
    'APCA-API-SECRET-KEY': secret,
  };
}

async function fetchAlpacaJson(path: string): Promise<{ data: Record<string, unknown>; url: string }> {
  const url = `${ALPACA_DATA_BASE_URL}${path}`;
  const response = await fetch(url, { headers: getAlpacaHeaders() });
  if (!response.ok) {
    throw new Error(`Alpaca request failed: ${response.status} ${response.statusText}`);
  }
  const data = (await response.json()) as Record<string, unknown>;
  return { data, url };
}

export const ALPACA_MARKET_DATA_DESCRIPTION = `
Free Alpaca market data fallback for equities. Retrieves OHLCV bars, latest quotes, and company news.

## When to Use
- Backup source when primary paid market data tools fail or are rate-limited
- Quick stock price history for a single ticker
- Latest quote checks before/after catalyst events
- Pulling recent headline context from Alpaca's stock news feed

## When NOT to Use
- Deep fundamentals (use get_financials or Yahoo fallback)
- SEC filing text extraction (use read_filings or web_fetch)
- Option chains (use get_yahoo_finance_data)
`.trim();

const AlpacaMarketDataInputSchema = z.object({
  action: z
    .enum(['bars', 'latest_quote', 'news'])
    .describe('Which Alpaca endpoint to query.'),
  ticker: z
    .string()
    .describe("Stock ticker symbol, for example 'LLY'."),
  timeframe: z
    .enum(['1Min', '5Min', '15Min', '1Hour', '1Day'])
    .default('1Day')
    .describe("Bars timeframe. Only used when action='bars'."),
  start: z
    .string()
    .optional()
    .describe("Optional ISO date/time start for bars, e.g. '2026-05-01' or RFC3339."),
  end: z
    .string()
    .optional()
    .describe("Optional ISO date/time end for bars."),
  limit: z
    .number()
    .int()
    .min(1)
    .max(1000)
    .default(100)
    .describe('Result limit for bars/news.'),
});

export const getAlpacaMarketData = new DynamicStructuredTool({
  name: 'get_alpaca_market_data',
  description:
    'Free Alpaca market data fallback for OHLCV bars, latest quote, and stock news. Use when paid market data tools fail or credits are depleted.',
  schema: AlpacaMarketDataInputSchema,
  func: async (input) => {
    const ticker = input.ticker.trim().toUpperCase();

    if (input.action === 'latest_quote') {
      const { data, url } = await fetchAlpacaJson(`/v2/stocks/${ticker}/quotes/latest?feed=iex`);
      return formatToolResult(data.quote || data, [url]);
    }

    if (input.action === 'news') {
      const params = new URLSearchParams({
        symbols: ticker,
        limit: String(input.limit),
        sort: 'desc',
      });
      const { data, url } = await fetchAlpacaJson(`/v1beta1/news?${params.toString()}`);
      return formatToolResult((data.news as unknown[]) || [], [url]);
    }

    const params = new URLSearchParams({
      timeframe: input.timeframe,
      adjustment: 'raw',
      feed: 'iex',
      limit: String(input.limit),
    });
    if (input.start) params.set('start', input.start);
    if (input.end) params.set('end', input.end);

    const { data, url } = await fetchAlpacaJson(`/v2/stocks/${ticker}/bars?${params.toString()}`);
    return formatToolResult((data.bars as unknown[]) || [], [url]);
  },
});
