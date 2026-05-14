#!/usr/bin/env bun
import { buildDiscordMessages } from './weekly-brief.js';

const inputTickers = ['LLY', 'VRTX', 'AMGN'];

const payload = {
  week_of: '2026-05-18',
  signals: [
    {
      ticker: 'VRTX',
      direction: 'long' as const,
      conviction: 78,
      thesis: 'Vertex has a PDUFA decision May 20 for vanzacaftor in cystic fibrosis. Insider buying activity over the past month and strong financial position support upside.',
      catalysts: [
        { date: '2026-05-20', event: 'PDUFA decision for vanzacaftor' },
        { date: '2026-05-22', event: 'Phase 3 KIDNEY-2 readout' },
      ],
      high_conviction: true,
    },
    {
      ticker: 'NEWBIO',
      direction: 'long' as const,
      conviction: 75,
      thesis: 'Discovered this week. Has a Phase 3 readout May 23 in oncology with strong precedent data.',
      catalysts: [{ date: '2026-05-23', event: 'Phase 3 oncology readout' }],
      high_conviction: true,
    },
    {
      ticker: 'LLY',
      direction: 'skip' as const,
      conviction: 35,
      thesis: 'No imminent catalysts.',
      catalysts: [],
      high_conviction: false,
    },
  ],
};

const newlyDiscovered = [
  {
    ticker: 'NEWBIO',
    company_name: 'NewBio Therapeutics',
    expected_catalyst: 'Phase 3 oncology readout 2026-05-23',
    discovery_source: 'dexter_brief',
  },
  {
    ticker: 'NOTYETBIO',
    company_name: 'NotYet Pharma',
    expected_catalyst: 'PDUFA decision 2026-06-01',
    discovery_source: 'dexter_brief',
  },
];

const messages = buildDiscordMessages(inputTickers, payload, 'Full report omitted for test.', newlyDiscovered);

console.log(`\n=== Generated ${messages.length} Discord message(s) ===\n`);
messages.forEach((msg, i) => {
  console.log(`--- Message ${i + 1} (${msg.length} chars) ---`);
  console.log(msg);
  console.log('');
});
