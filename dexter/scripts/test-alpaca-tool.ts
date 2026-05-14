#!/usr/bin/env bun
import { config } from 'dotenv';
import { getAlpacaMarketData } from '../src/tools/finance/alpaca-data.js';

config({ quiet: true });

async function main(): Promise<void> {
  const ticker = (process.argv[2] || 'AAPL').toUpperCase();

  const bars = await getAlpacaMarketData.invoke({
    action: 'bars',
    ticker,
    timeframe: '1Day',
    limit: 5,
  });

  const news = await getAlpacaMarketData.invoke({
    action: 'news',
    ticker,
    limit: 3,
  });

  console.log('Bars response:', typeof bars === 'string' ? bars.slice(0, 300) : bars);
  console.log('News response:', typeof news === 'string' ? news.slice(0, 300) : news);
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
});
