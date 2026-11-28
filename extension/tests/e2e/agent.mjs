import { chromium } from 'playwright';
import { pages } from './mock.mjs';
import assert from 'node:assert/strict';
import fs from 'node:fs';
const EXT = new URL('../../.output/chrome-mv3', import.meta.url).pathname;
const ctx = await chromium.launchPersistentContext(fs.mkdtempSync('/tmp/pw-ext-'), { channel: 'chromium', headless: true, args: [`--disable-extensions-except=${EXT}`, `--load-extension=${EXT}`] });
let [sw] = ctx.serviceWorkers(); if (!sw) sw = await ctx.waitForEvent('serviceworker');

const FIT = { founder_amy: 90, dev_bob: 85, reader_one: 75, reader_two: 30, sam_k: 20 };
const llm = { calls: [] };
await ctx.route('https://api.deepseek.com/**', async (route) => {
  const body = JSON.parse(route.request().postData());
  const sys = body.messages[0].content, usr = body.messages[1].content;
  llm.calls.push(sys.slice(0, 40));
  let out = {};
  if (sys.includes('qualify Reddit authors')) {
    out = { results: [...usr.matchAll(/\[id=([^\]]+)\] (?:post|comment) by u\/(\S+)/g)].map(([, id, a]) => ({ id, fit: FIT[a] ?? 10, intent: 'asking', reason: `${a} asked for help (mock)`, dm_ok: true, comment_fit: id === '/r/saas/comments/p1/t1/' })) };
  } else if (sys.includes('write a Reddit comment') || sys.includes('writes a Reddit comment') || sys.includes('You write a Reddit comment')) {
    out = { comment: 'Mock helpful reply that answers the question.', why: 'they asked directly' };
  }
  await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ choices: [{ message: { content: JSON.stringify(out) } }] }) });
});
await ctx.route('https://www.reddit.com/**', (route) => {
  const u = new URL(route.request().url());
  const html = u.pathname.startsWith('/user/') ? pages.profile(u.pathname.split('/')[2]) : u.pathname.includes('/comments/') ? pages.postPage() : pages.listing();
  route.fulfill({ contentType: 'text/html', body: html });
});

const seed = (extra = {}) => sw.evaluate(async (extra) => {
  await chrome.storage.local.clear();
  await chrome.storage.local.set({
    settings: { deepseekKey: 'sk-test', model: 'deepseek-flash', dailyDmCap: 40, warmup: true, warmupStart: Date.now(), activeStart: 0, activeEnd: 24, minGapSec: 60, maxGapSec: 90, minFit: 70, sessionMinutes: 0.05, upvotes: true, maxUpvotesPerSession: 5, dmMode: 'template', approveAiDrafts: true, aiGuidance: '', autoSchedule: false, sessionsPerDay: 3, myUsername: '', ...extra },
    product: { url: 'https://x.com', name: 'Pigeon', summary: 'finds customers on reddit', offer: 'a demo', icp: 'founders', personas: [], painPhrases: [], buyingSignals: [], competitors: [], subredditIdeas: [], searchQueries: [] },
    subreddits: [{ name: 'saas', subscribers: 1, postsPerDay: 1, icpDensity: 80, rulesRisk: 'low', score: 80, reason: '', enabled: true }],
    templates: [{ id: 'default', name: 'Default', body: '{Hey|Hi} {name}, saw your post in r/{subreddit} about {topic}. {Happy to share|Glad to help} if useful?' }],
  });
}, extra);
const get = (k) => sw.evaluate(async (k) => (await chrome.storage.local.get(k))[k], k);
const waitIdle = async (ms) => { const end = Date.now() + ms; while (Date.now() < end) { const a = await get('agent'); if (a && a.running === false && a.phase === 'idle') return a; await new Promise((r) => setTimeout(r, 1000)); } throw new Error('agent never finished; log=' + JSON.stringify((await get('log')).slice(-8))); };
const extId = new URL(sw.url()).host;
const panel = await ctx.newPage(); await panel.goto(`chrome-extension://${extId}/sidepanel.html`);
const msg = (m) => panel.evaluate((m) => chrome.runtime.sendMessage(m), m);
const start = (mode) => msg({ type: 'agent:start', mode });
const t0 = Date.now(); const lap = (m) => console.log(`✓ ${m} (${((Date.now() - t0) / 1000).toFixed(0)}s)`);

// ========== Scenario 1: full autopilot session
await seed();
const r = await start('full'); assert.ok(r.ok, JSON.stringify(r));
const final = await waitIdle(360000);
const log = (await get('log')).map((l) => l.msg);
console.log('--- activity log ---\n' + log.map((l) => '  ' + l).join('\n') + '\n--------------------');
const leads = Object.entries(await sw.evaluate(() => chrome.storage.local.get(null))).filter(([k]) => k.startsWith('lead:')).map(([, v]) => v);
assert.deepEqual(leads.map((l) => l.username).sort(), ['dev_bob', 'founder_amy', 'reader_one'], 'only fit>=70 humans become leads (bot + low-fit excluded)');
lap('browsed r/saas, scored people, created exactly the right leads');
const amy = leads.find((l) => l.username === 'founder_amy');
// P0-4 (owner decision): every message waits for approval, template included. Previously a template DM went straight out.
assert.equal(amy.status, 'draft', JSON.stringify(amy)); assert.ok(/^(Hey|Hi) founder_amy, saw your post in r\/saas about how do I find my first 10 customers\. (Happy to share|Glad to help) if useful\?$/.test(amy.message), amy.message);
lap('highest-fit lead got a rendered, spun template as a draft awaiting approval: "' + amy.message + '"');
assert.equal(leads.filter((l) => l.status === 'sent').length, 0, 'nothing sent without approval');
assert.ok(final.stats.scanned >= 3 && final.stats.upvotes >= 1 && final.stats.dms === 0, JSON.stringify(final.stats));
lap(`stats ${JSON.stringify(final.stats)}`);
const opps = await get('opportunities'); assert.equal(opps.length, 1); assert.equal(opps[0].status, 'pending');
lap('drafted 1 comment opportunity, left pending for approval');
// approve amy's draft, then a DM-only session sends exactly that one and the governor books it
await sw.evaluate(async () => { const k = 'lead:founder_amy'; const l = (await chrome.storage.local.get(k))[k]; await chrome.storage.local.set({ [k]: { ...l, status: 'ready' } }); });
await start('dm'); await waitIdle(120000);
assert.equal((await get('lead:founder_amy')).status, 'sent');
const daily = await get('daily'); assert.equal(daily.count, 1); assert.ok(daily.nextDmAt > Date.now() + 50000, 'next DM scheduled 60-90s out');
lap('after approval, the DM went out; governor recorded it and scheduled the next one');
assert.ok(log.some((l) => /DMs held|Waiting|Session finished/.test(l)));
assert.equal((await ctx.pages()).length >= 1, true);

// ========== Scenario 2: stop button works mid-session
await seed({ sessionMinutes: 10 });
await start('find');
await new Promise((r) => setTimeout(r, 12000));
assert.equal((await get('agent')).running, true);
await msg({ type: 'agent:stop' });
const t1 = Date.now(); await waitIdle(30000);
lap(`Stop halted a 10-minute session in ${((Date.now() - t1) / 1000).toFixed(1)}s`);
assert.ok((await get('log')).some((l) => l.msg === 'Stopped'));

// ========== Scenario 3: rate limit pauses DMs and stops the session
await seed({ sessionMinutes: 0.05 });
await sw.evaluate(async () => { await chrome.storage.local.set({ 'lead:ratelimited': { username: 'ratelimited', subreddit: 'saas', postUrl: '', postTitle: 't', snippet: '', fit: 99, intent: 'x', reason: 'r', status: 'ready', message: 'hello there, quick question for you', source: 'csv', createdAt: Date.now() } }); });
await start('dm'); await waitIdle(120000);
const rl = await get('lead:ratelimited'); const d2 = await get('daily');
assert.equal(rl.status, 'failed'); assert.ok(d2.pausedUntil > Date.now() + 5 * 3600e3, 'paused ~6h');
lap('rate-limit banner → lead failed, DMs paused for ~6h, session stopped');
await ctx.close();
console.log('\nAGENT LOOP E2E PASSED');
