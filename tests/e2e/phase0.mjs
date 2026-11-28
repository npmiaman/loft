// Phase 0 (safety) end to end: approvals, in-panel confirms, never re-DM, one DM cap + pause banner.
// Real Chromium + the built extension; Reddit and DeepSeek are mocked. Run `npm run build` first.
import { chromium } from 'playwright';
import { pages } from './mock.mjs';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const EXT = new URL('../../.output/chrome-mv3', import.meta.url).pathname;
const SHOTS = process.env.SHOTS ?? '';
const ctx = await chromium.launchPersistentContext(fs.mkdtempSync(path.join(os.tmpdir(), 'pw-ext-')), { channel: 'chromium', headless: true, viewport: { width: 400, height: 900 }, args: [`--disable-extensions-except=${EXT}`, `--load-extension=${EXT}`] });
let [sw] = ctx.serviceWorkers(); if (!sw) sw = await ctx.waitForEvent('serviceworker');
const extId = new URL(sw.url()).host;
const t0 = Date.now(); const lap = (m) => console.log(`✓ ${m} (${((Date.now() - t0) / 1000).toFixed(0)}s)`);

const FIT = { founder_amy: 90, dev_bob: 85, reader_one: 75, reader_two: 30, sam_k: 20 };
await ctx.route('https://api.deepseek.com/**', async (route) => {
  const body = JSON.parse(route.request().postData());
  const sys = body.messages[0].content, usr = body.messages[1].content;
  let out = {};
  if (sys.includes('qualify Reddit authors')) out = { results: [...usr.matchAll(/\[id=([^\]]+)\] (?:post|comment) by u\/(\S+)/g)].map(([, id, a]) => ({ id, fit: FIT[a] ?? 10, intent: 'asking', reason: `${a} asked for help (mock)`, dm_ok: true, comment_fit: false })) };
  await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ choices: [{ message: { content: JSON.stringify(out) } }] }) });
});
await ctx.route('https://www.reddit.com/**', (route) => {
  const u = new URL(route.request().url());
  if (u.pathname.endsWith('.json')) return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ data: {} }) });
  const html = u.pathname.startsWith('/user/') ? pages.profile(u.pathname.split('/')[2]) : u.pathname.includes('/comments/') ? pages.postPage() : pages.listing();
  route.fulfill({ contentType: 'text/html', body: html });
});

const SETTINGS = { deepseekKey: 'sk-test', model: 'deepseek-flash', dailyDmCap: 40, warmup: false, warmupStart: Date.now(), activeStart: 0, activeEnd: 24, minGapSec: 60, maxGapSec: 90, minFit: 70, sessionMinutes: 0.05, upvotes: false, maxUpvotesPerSession: 0, dmMode: 'template', approveAiDrafts: false, aiGuidance: '', autoSchedule: false, sessionsPerDay: 3, myUsername: 'me_tester', pace: 1.8 };
const PRODUCT = { url: 'https://x.com', name: 'Pigeon', summary: 'finds customers on reddit', offer: 'a demo', icp: 'founders', personas: [], painPhrases: [], buyingSignals: [], competitors: [], subredditIdeas: [], searchQueries: [] };
const SUBS = [{ name: 'saas', subscribers: 1, postsPerDay: 1, icpDensity: 80, rulesRisk: 'low', score: 80, reason: '', enabled: true }];
const TEMPLATES = [{ id: 'default', name: 'Default', body: 'Hi {name}, saw your post in r/{subreddit} about {topic}. Happy to share if useful?' }];
const lead = (username, status, message, extra = {}) => ({ username, subreddit: 'saas', postUrl: '', postTitle: 't', snippet: '', fit: 80, intent: 'x', reason: 'r', status, message, source: 'csv', createdAt: Date.now(), ...extra });
const seed = (extra = {}, settings = {}) => sw.evaluate(async ([extra, base]) => {
  await chrome.storage.local.clear();
  await chrome.storage.local.set({ onboarded: true, settings: base, product: extra.product, subreddits: extra.subreddits, templates: extra.templates, ...extra.kv });
}, [{ product: PRODUCT, subreddits: SUBS, templates: TEMPLATES, kv: extra }, { ...SETTINGS, ...settings }]);
const get = (k) => sw.evaluate(async (k) => (await chrome.storage.local.get(k))[k], k);
const allLeads = async () => Object.entries(await sw.evaluate(() => chrome.storage.local.get(null))).filter(([k]) => k.startsWith('lead:')).map(([, v]) => v);
const waitIdle = async (ms) => { const end = Date.now() + ms; await new Promise((r) => setTimeout(r, 1200)); while (Date.now() < end) { const a = await get('agent'); if (a && !a.running && a.phase === 'idle') return a; await new Promise((r) => setTimeout(r, 800)); } throw new Error('agent never finished; log=' + JSON.stringify((await get('log')).slice(-8))); };

const panel = await ctx.newPage();
panel.on('pageerror', (e) => console.log('PAGE ERROR', e.message));
panel.on('dialog', (d) => { d.dismiss(); throw new Error('native browser dialog used: ' + d.message()); });
// the panel remembers the last tab, so every open starts from Home explicitly
const open = async () => { await panel.goto(`chrome-extension://${extId}/sidepanel.html`); await panel.waitForSelector('nav.tabs'); await panel.locator('nav.tabs').getByRole('button', { name: 'Home' }).click(); };
const send = (m) => panel.evaluate((m) => chrome.runtime.sendMessage(m), m);
// Navigation helper (phase 3 layout: Home / Review [Waiting, DMs, Comments, Posts] / Setup / Warm-up, Settings gear).
const go = async (where) => {
  if (where === 'home') return panel.locator('nav.tabs').getByRole('button', { name: 'Home' }).click();
  if (where === 'settings') return panel.getByRole('button', { name: 'Settings' }).click();
  await panel.locator('nav.tabs').getByRole('button', { name: /^Review/ }).click();
  const f = { dms: /^DMs/, comments: /^Comments/, posts: /^Posts/ }[where];
  await panel.locator('.seg.filter').getByRole('button', { name: f }).click();
};
const dialog = () => panel.getByRole('dialog');
const shot = async (n) => SHOTS && panel.screenshot({ path: path.join(SHOTS, `p0-${n}.png`), fullPage: true });

// ============ P0-4: every message waits for approval, template included; Start says what it will send
await seed();
await open();
await panel.locator('.run-summary', { hasText: /Will send up to 0 approved messages today \(limit 40, 0 sent\)/ }).waitFor({ timeout: 5000 });
assert.equal(await panel.getByLabel('Skip AI scoring (queue everyone)').isChecked(), false, '"Skip AI scoring" defaults off');
lap('Start states what it will send (real cap); "Skip AI scoring" defaults off');
let r = await send({ type: 'agent:start', mode: 'full' }); assert.ok(r.ok, JSON.stringify(r));
let st = await waitIdle(360000);
let leads = await allLeads();
const amy = leads.find((l) => l.username === 'founder_amy');
assert.equal(amy.status, 'draft', 'a template message is a draft, not ready: ' + JSON.stringify(amy));
assert.ok(/^Hi founder_amy/.test(amy.message));
assert.equal(st.stats.dms, 0, 'Autopilot sent nothing that was not approved');
assert.equal(leads.filter((l) => l.status === 'sent' || l.status === 'ready').length, 0);
assert.ok((await get('log')).some((l) => /Draft waiting for your approval: u\/founder_amy/.test(l.msg)));
lap('Autopilot with a template: message written as a draft, nothing sent');

// ============ P0-1 + P0-5: approve/send use the on-screen text; in-panel confirms
await seed({ 'lead:drafty': lead('drafty', 'draft', 'old saved text'), 'lead:readyone': lead('readyone', 'ready', 'old ready text'), 'lead:d2': lead('d2', 'draft', 'second draft'), 'lead:d3': lead('d3', 'draft', 'third draft') });
await open(); await go('dms');
const item = (u) => panel.locator('.item', { has: panel.getByRole('link', { name: `u/${u}` }) });
assert.equal(await item('drafty').getByRole('button', { name: 'Send now' }).count(), 0, 'no plain "Send now" on an unapproved draft');
await item('drafty').locator('textarea').fill('edited on screen');
await item('drafty').getByRole('button', { name: 'Approve', exact: true }).click();
await panel.waitForTimeout(400);
let drafty = await get('lead:drafty');
assert.equal(drafty.message, 'edited on screen'); assert.equal(drafty.status, 'ready');
lap('Approve saves the on-screen edit first');

await item('readyone').locator('textarea').fill('ready text, edited');
await item('readyone').getByRole('button', { name: 'Send now' }).click();
await dialog().waitFor();
assert.match(await dialog().innerText(), /u\/readyone/); assert.match(await dialog().innerText(), /ready text, edited/, 'confirm shows what will be sent');
await shot('confirm-send');
await dialog().getByRole('button', { name: 'Cancel' }).click();
assert.equal((await get('lead:readyone')).status, 'ready', 'cancel sends nothing');
await item('readyone').getByRole('button', { name: 'Send now' }).click();
await dialog().getByRole('button', { name: 'Send', exact: true }).click();
for (let i = 0; i < 120 && (await get('lead:readyone')).status === 'ready'; i++) await panel.waitForTimeout(500);
const ro = await get('lead:readyone');
assert.equal(ro.status, 'sent', JSON.stringify(ro)); assert.equal(ro.message, 'ready text, edited', 'the text on screen is what was sent');
lap('Send now: in-panel confirm with the text; sends the on-screen edit');

await item('d2').getByRole('button', { name: 'Approve & send' }).waitFor();
await panel.getByRole('button', { name: /^Approve all/ }).click();
assert.match(await dialog().innerText(), /2 drafts/, 'approve all shows the count');
await dialog().getByRole('button', { name: 'Cancel' }).click();
assert.equal((await get('lead:d2')).status, 'draft');
await panel.getByRole('button', { name: /^Approve all/ }).click();
await dialog().getByRole('button', { name: /^Approve 2/ }).click();
await panel.waitForTimeout(500);
assert.equal((await get('lead:d2')).status, 'ready'); assert.equal((await get('lead:d3')).status, 'ready');
lap('Approve all: confirm shows the count');

// ============ P0-6: send -> clear -> rescan: never re-queued
await panel.getByRole('button', { name: 'Clear all leads' }).click();
assert.match(await dialog().innerText(), /already messaged/i);
await dialog().getByRole('button', { name: 'Clear all' }).click();
await panel.waitForTimeout(500);
assert.equal((await allLeads()).length, 0);
assert.ok((await get('contacted')).includes('readyone'), 'contacted set survives clearing');
await seed({ contacted: ['founder_amy'] });
r = await send({ type: 'agent:start', mode: 'find' }); assert.ok(r.ok); await waitIdle(360000);
leads = await allLeads();
assert.ok(leads.some((l) => l.username === 'dev_bob'), 'scan worked: ' + leads.map((l) => l.username));
assert.ok(!leads.some((l) => l.username === 'founder_amy'), 'already-messaged person was not re-queued');
// clear unsent keeps the sent ones
await seed({ 'lead:sent1': lead('sent1', 'sent', 'x', { sentAt: Date.now() }), 'lead:q1': lead('q1', 'queued', '') });
await open(); await go('dms');
await panel.getByRole('button', { name: 'Clear unsent leads' }).click();
await dialog().getByRole('button', { name: 'Clear unsent' }).click();
await panel.waitForTimeout(500);
assert.deepEqual((await allLeads()).map((l) => l.username), ['sent1']);
lap('send -> clear -> rescan: never re-queued; "Clear unsent leads" keeps sent ones');

// comment approve & post confirm names the subreddit
await seed({ opportunities: [{ id: 'o1', postUrl: 'https://www.reddit.com/r/saas/comments/p1/t1/', title: 'How do I find customers?', subreddit: 'saas', author: 'founder_amy', draft: 'a helpful reply', why: 'asked', status: 'pending', createdAt: Date.now() }] });
await open(); await go('comments');
await panel.getByRole('button', { name: 'Approve & post' }).click();
assert.match(await dialog().innerText(), /Posting in r\/saas/);
await dialog().getByRole('button', { name: 'Cancel' }).click();
assert.equal((await get('opportunities'))[0].status, 'pending');
lap('Approve & post asks first and names the subreddit');

// ============ P0-10: one cap (plan aware) + pause banner with Resume
const plan = { name: 'P', summary: '', phases: [{ name: 'Lurk', fromDay: 1, toDay: 5, goal: '', quota: { browse: 5, upvote: 1, comment: 0, post: 0, join: 0, dm: 0 }, notes: '' }], subreddits: [], voice: '', rules: [], avoid: [], sessionsPerDay: 1, manualSteps: [], assumptions: [], warnings: [], builtAt: 1 };
await seed({ warmup: { enabled: true, startedAt: Date.now(), docs: [], plan, approval: 'review' } });
await open();
await panel.locator('.dm-cap', { hasText: /0 \/ 0 · warm-up plan phase "Lurk"/ }).waitFor({ timeout: 5000 });
await panel.locator('.banner', { hasText: /No DMs today: warm-up plan phase "Lurk"/ }).waitFor({ timeout: 5000 });
lap('Home shows the plan cap (0, "Lurk") the governor enforces, with a banner');

const tomorrow = new Date(); tomorrow.setDate(tomorrow.getDate() + 1); tomorrow.setHours(10, 42, 0, 0);
await seed({ daily: { date: new Date().toISOString().slice(0, 10), count: 0, sessions: 0, nextDmAt: 0, pausedUntil: tomorrow.getTime(), pauseReason: 'Reddit showed a captcha' } });
await open();
await panel.locator('.banner', { hasText: 'captcha' }).waitFor({ timeout: 5000 });
const banner = await panel.locator('.banner').innerText();
assert.match(banner, /until tomorrow 10:42/); assert.match(banner, /captcha/);
await shot('banner');
await panel.getByRole('button', { name: 'Resume now' }).click();
for (let i = 0; i < 20 && (await get('daily')).pausedUntil; i++) await panel.waitForTimeout(200);
assert.equal((await get('daily')).pausedUntil, 0);
await panel.locator('.banner').waitFor({ state: 'detached' });
lap('pause banner: reason + "until tomorrow 10:42"; Resume now clears it');

await seed({ subreddits: [] });
await open();
await send({ type: 'agent:start', mode: 'full' }); await waitIdle(30000);
await panel.locator('.run-card .notice.error').waitFor();
assert.match(await panel.locator('.run-card .notice.error').innerText(), /No subreddits enabled/);
lap('a session error shows on the run card, not only in the log');

await ctx.close();
console.log('\nPHASE 0 E2E PASSED');
