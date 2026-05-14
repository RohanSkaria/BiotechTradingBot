import { DynamicStructuredTool } from '@langchain/core/tools';
import { z } from 'zod';
import YahooFinance from 'yahoo-finance2';
import { formatToolResult } from '../types.js';

const yahooFinance = new YahooFinance();

export const YAHOO_FINANCE_DATA_DESCRIPTION = `
Free Yahoo Finance fallback for quotes, fundamentals, historical bars, recommendations, options chains, and search.

## When to Use
- Free replacement for fundamentals and historical price data when paid providers fail
- Options-chain and implied-volatility context around biotech catalyst dates
- Analyst recommendation snapshots and basic company profile checks
- Quick ticker discovery/search without API keys

## When NOT to Use
- Primary source for strict production-grade pricing (prefer paid provider when available)
- SEC filing text extraction (use read_filings or web_fetch)
- Ultra-low-latency quote streaming
`.trim();

const YahooFinanceDataInputSchema = z.object({
  action: z
    .enum(['quote', 'quote_summary', 'historical', 'recommendations', 'options', 'search'])
    .describe('Yahoo Finance data action to perform.'),
  ticker: z
    .string()
    .optional()
    .describe("Ticker symbol, for example 'VRTX'. Required for all actions except search."),
  query: z
    .string()
    .optional()
    .describe("Search query. Required when action='search'."),
  period1: z
    .string()
    .optional()
    .describe("Historical start date, e.g. '2025-01-01'. Only used when action='historical'."),
  period2: z
    .string()
    .optional()
    .describe("Historical end date, e.g. '2026-01-01'. Only used when action='historical'."),
  strike: z
    .number()
    .optional()
    .describe("Optional strike filter for options query. Applied client-side when action='options'."),
});

const QUOTE_SUMMARY_MODULES = [
  'incomeStatementHistory',
  'balanceSheetHistory',
  'cashflowStatementHistory',
  'defaultKeyStatistics',
  'financialData',
  'assetProfile',
] as const;

function requireTicker(ticker: string | undefined): string {
  const symbol = ticker?.trim().toUpperCase();
  if (!symbol) {
    throw new Error('ticker is required for this get_yahoo_finance_data action');
  }
  return symbol;
}

export const getYahooFinanceData = new DynamicStructuredTool({
  name: 'get_yahoo_finance_data',
  description:
    'Free Yahoo Finance fallback for quotes, fundamentals, historical bars, recommendations, options chains, and search.',
  schema: YahooFinanceDataInputSchema,
  func: async (input) => {
    if (input.action === 'search') {
      if (!input.query?.trim()) {
        throw new Error("query is required when action='search'");
      }
      const result = await yahooFinance.search(input.query.trim());
      return formatToolResult(result, ['https://finance.yahoo.com']);
    }

    const ticker = requireTicker(input.ticker);

    if (input.action === 'quote') {
      const result = await yahooFinance.quote(ticker);
      return formatToolResult(result, [`https://finance.yahoo.com/quote/${ticker}`]);
    }

    if (input.action === 'quote_summary') {
      const result = await yahooFinance.quoteSummary(ticker, {
        modules: [...QUOTE_SUMMARY_MODULES],
      });
      return formatToolResult(result, [`https://finance.yahoo.com/quote/${ticker}/financials`]);
    }

    if (input.action === 'historical') {
      if (!input.period1 || !input.period2) {
        throw new Error("period1 and period2 are required when action='historical'");
      }
      const result = await yahooFinance.historical(ticker, {
        period1: input.period1,
        period2: input.period2,
      });
      return formatToolResult(result, [`https://finance.yahoo.com/quote/${ticker}/history`]);
    }

    if (input.action === 'recommendations') {
      const result = await yahooFinance.recommendationsBySymbol(ticker);
      return formatToolResult(result, [`https://finance.yahoo.com/quote/${ticker}/analysis`]);
    }

    const result = (await yahooFinance.options(ticker)) as {
      calls?: Array<{ strike?: number }>;
      puts?: Array<{ strike?: number }>;
      [key: string]: unknown;
    };
    if (!input.strike) {
      return formatToolResult(result, [`https://finance.yahoo.com/quote/${ticker}/options`]);
    }

    const strike = input.strike;
    const filtered = {
      ...result,
      calls: (result.calls || []).filter((c) => c.strike === strike),
      puts: (result.puts || []).filter((p) => p.strike === strike),
    };
    return formatToolResult(filtered, [`https://finance.yahoo.com/quote/${ticker}/options`]);
  },
});
