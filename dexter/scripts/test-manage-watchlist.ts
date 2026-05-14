#!/usr/bin/env bun
import { config } from 'dotenv';
import { manageWatchlistTool } from '../src/tools/finance/manage-watchlist.js';

config({ quiet: true });

async function main(): Promise<void> {
  const beforeList = await manageWatchlistTool.invoke({ action: 'list_active' });
  console.log('Before list_active:', typeof beforeList === 'string' ? beforeList.slice(0, 500) : beforeList);

  const expire = await manageWatchlistTool.invoke({ action: 'expire_stale', stale_days: 14 });
  console.log('Expire result:', typeof expire === 'string' ? expire : expire);

  const addOne = await manageWatchlistTool.invoke({
    action: 'add',
    ticker: 'DXTRTEST',
    company_name: 'Dexter Smoke Test Bio',
    expected_catalyst: 'Smoke test catalyst (auto-inserted)',
    market_cap_tier: 'small',
    priority: 'low',
  });
  console.log('Add result:', typeof addOne === 'string' ? addOne : addOne);

  const addAgain = await manageWatchlistTool.invoke({
    action: 'add',
    ticker: 'DXTRTEST',
    expected_catalyst: 'Updated catalyst on second add',
  });
  console.log('Re-add (upsert) result:', typeof addAgain === 'string' ? addAgain : addAgain);

  const afterList = await manageWatchlistTool.invoke({ action: 'list_active' });
  console.log('After list_active:', typeof afterList === 'string' ? afterList.slice(0, 800) : afterList);
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
});
