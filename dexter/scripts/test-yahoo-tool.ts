#!/usr/bin/env bun
import { config } from 'dotenv';
import { getYahooFinanceData } from '../src/tools/finance/yahoo-data.js';

config({ quiet: true });

async function main(): Promise<void> {
  const ticker = (process.argv[2] || 'AAPL').toUpperCase();

  const quote = await getYahooFinanceData.invoke({
    action: 'quote',
    ticker,
  });

  const summary = await getYahooFinanceData.invoke({
    action: 'quote_summary',
    ticker,
  });

  const options = await getYahooFinanceData.invoke({
    action: 'options',
    ticker,
  });

  console.log('Quote response:', typeof quote === 'string' ? quote.slice(0, 300) : quote);
  console.log('Summary response:', typeof summary === 'string' ? summary.slice(0, 300) : summary);
  console.log('Options response:', typeof options === 'string' ? options.slice(0, 300) : options);
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
});
