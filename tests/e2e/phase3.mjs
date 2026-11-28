// Phase 3 (UX + UI) end to end: 5-section layout, first run, warm-up vocabulary, design system, validation, HUD.
// Real Chromium + the built extension; Reddit and DeepSeek are mocked. Run `npm run build` first.
import { chromium } from 'playwright';
import { pages } from './mock.mjs';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const EXT = new URL('../../.output/chrome-mv3', import.meta.url).pathname;
const SHOTS = process.env.SHOTS ?? '';
const ctx = await chromium.launchPersistentContext(fs.mkdtempSync(path.join(os.tmpdir(), 'pw-ext-')), { channel: 'chromium', headless: true, viewport: { width: 360, height: 800 }, args: [`--disable-extensions-except=${EXT}`, `--load-extension=${EXT}`] });
let [sw] = ctx.serviceWorkers(); if (!sw) sw = await ctx.waitForEvent('serviceworker');
const extId = new URL(sw.url()).host;
const t0 = Date.now(); const lap = (m) => console.log(`✓ ${m} (${((Date.now() - t0) / 1000).toFixed(0)}s)`);

const llm = { keys: [] };
await ctx.route('https://api.deepseek.com/**', async (route) => {
  const auth = route.request().headers().authorization ?? '';
  llm.keys.push(auth);
  if (auth.includes('sk-bad')) return route.fulfill({ status: 401, contentType: 'application/json', body: '{"error":{"message":"bad key"}}' });
  await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ choices: [{ message: { content: 'OK' } }] }) });
});
await ctx.route('https://www.reddit.com/**', (route) => {
  const u = new URL(route.request().url());
  if (u.pathname === '/api/me.json') return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ data: { name: 'pigeon_tester', total_karma: 42 } }) });
  if (u.pathname.endsWith('.json')) return route.fulfill({ contentType: 'application/json', body: '{}' });
  const html = u.pathname.startsWith('/user/') ? pages.profile(u.pathname.split('/')[2]) : u.pathname.includes('/comments/') ? pages.postPage() : pages.listing();
  route.fulfill({ contentType: 'text/html', body: html });
});

const SETTINGS = { deepseekKey: 'sk-test', model: 'deepseek-flash', dailyDmCap: 40, warmup: true, warmupStart: Date.now(), activeStart: 9, activeEnd: 21, minGapSec: 60, maxGapSec: 180, minFit: 70, sessionMinutes: 30, upvotes: true, maxUpvotesPerSession: 10, dmMode: 'template', aiGuidance: '', autoSchedule: false, sessionsPerDay: 3, myUsername: 'me_tester', pace: 1 };
const PRODUCT = { url: 'https://x.com', name: 'Pigeon', summary: 's', offer: 'o', icp: 'founders', personas: [], painPhrases: [], buyingSignals: [], competitors: [], subredditIdeas: [], searchQueries: [] };
const SUBS = [{ name: 'saas', subscribers: 1, postsPerDay: 1, icpDensity: 80, rulesRisk: 'low', score: 80, reason: '', enabled: true }];
const PLAN = { name: 'P', summary: '', phases: [{ name: 'Lurk', fromDay: 1, toDay: 5, goal: '', quota: { browse: 5, upvote: 1, comment: 0, post: 0, join: 0, dm: 0 }, notes: '' }], subreddits: [], voice: '', rules: [], avoid: [], sessionsPerDay: 1, manualSteps: [], assumptions: [], warnings: [], builtAt: 1 };
const lead = (username, status, fit, createdAt, message = 'hello there') => ({ username, subreddit: 'saas', postUrl: '', postTitle: 'a post', snippet: '', fit, intent: 'x', reason: `reason for ${username}`, status, message, source: 'csv', createdAt });
const seed = (kv = {}) => sw.evaluate(async (kv) => { await chrome.storage.local.clear(); await chrome.storage.local.set(kv); }, kv);
const full = (extra = {}) => seed({ settings: SETTINGS, product: PRODUCT, subreddits: SUBS, onboarded: true, ...extra });
const get = (k) => sw.evaluate(async (k) => (await chrome.storage.local.get(k))[k], k);

const panel = await ctx.newPage();
panel.on('pageerror', (e) => { throw new Error('PAGE ERROR ' + e.message); });
panel.on('dialog', (d) => { d.dismiss(); throw new Error('native dialog: ' + d.message()); });
const open = async () => { await panel.goto(`chrome-extension://${extId}/sidepanel.html`); await panel.waitForSelector('nav.tabs'); };
const nav = (name) => panel.locator('nav.tabs').getByRole('button', { name: new RegExp('^' + name) });
const shot = async (n) => SHOTS && panel.screenshot({ path: path.join(SHOTS, `p3-${n}.png`), fullPage: true });
const shots = async (n) => {
  if (!SHOTS) return;
  for (const w of [360, 420]) for (const scheme of ['light', 'dark']) {
    await panel.setViewportSize({ width: w, height: 800 }); await panel.emulateMedia({ colorScheme: scheme });
    await panel.screenshot({ path: path.join(SHOTS, `p3-${n}-${w}-${scheme}.png`), fullPage: true });
  }
  await panel.setViewportSize({ width: 360, height: 800 }); await panel.emulateMedia({ colorScheme: 'light' });
};

// ============ P3-2 first run: step by step, Reddit login check, Start says what's missing
await seed();
await open();
assert.match(await panel.locator('.steps li.now').innerText(), /Add your DeepSeek API key/);
assert.equal(await panel.getByRole('button', { name: 'Start', exact: true }).isDisabled(), true);
assert.match(await panel.locator('.start-needs').innerText(), /Start needs: Add your DeepSeek API key/);
await shots('first-run');
await panel.getByLabel('DeepSeek API key').fill('sk-bad');
await panel.getByRole('button', { name: 'Test key' }).click();
await panel.getByText(/rejected this key/).waitFor();
await panel.getByLabel('DeepSeek API key').fill('sk-good');
await panel.getByRole('button', { name: 'Test key' }).click();
await panel.locator('.steps li.done', { hasText: 'Add your DeepSeek API key' }).waitFor(); // a working key completes the step
assert.ok(llm.keys.includes('Bearer sk-good'));
assert.equal((await get('settings')).deepseekKey, 'sk-good');
await panel.locator('.steps li.now', { hasText: 'Describe your product' }).waitFor();
lap('first run: key step with Test key (bad key rejected clearly, good key accepted), then moves on');
await sw.evaluate(async ([p, s]) => chrome.storage.local.set({ product: p, subreddits: s }), [PRODUCT, SUBS]);
await panel.locator('.steps li.now', { hasText: 'safety' }).waitFor();
await panel.getByText('Reddit: logged in as u/pigeon_tester').waitFor();
await panel.getByRole('button', { name: 'Use u/pigeon_tester' }).click();
await panel.getByRole('button', { name: 'Looks good' }).click();
await panel.locator('.steps').waitFor({ state: 'detached' });
assert.equal((await get('settings')).myUsername, 'pigeon_tester');
assert.equal(await panel.getByRole('button', { name: 'Start', exact: true }).isDisabled(), false);
lap('first run: Reddit login read from reddit.com (logged in as u/pigeon_tester), username saved, checklist done');

// ============ P3-1 five sections, nothing overflows a 360px panel, last tab remembered
await full({
  'lead:amy': lead('amy', 'draft', 72, 3), 'lead:bob': lead('bob', 'draft', 90, 1), 'lead:cat': lead('cat', 'sent', 60, 2),
  opportunities: [{ id: 'o1', postUrl: 'https://www.reddit.com/r/saas/comments/p1/t1/', title: 'Opp title', subreddit: 'saas', author: 'z', draft: 'comment draft', why: 'w', status: 'pending', createdAt: 1 }],
  warmupItems: [{ id: 'w1', kind: 'comment', subreddit: 'saas', postTitle: 'thread', body: 'warm comment', why: '', status: 'pending', createdAt: 1 }, { id: 'w2', kind: 'post', subreddit: 'saas', title: 'Post title', body: 'post body', why: '', status: 'pending', createdAt: 1 }],
});
await open();
const labels = await panel.locator('nav.tabs button').allInnerTexts();
assert.deepEqual(labels.map((l) => l.replace(/\d+$/, '').trim()), ['Home', 'Review', 'Setup', 'Warm-up']);
for (const b of await panel.locator('nav.tabs button').all()) { const bb = await b.boundingBox(); assert.ok(bb.x >= 0 && bb.x + bb.width <= 360, 'tab fits in 360px'); }
assert.ok(await panel.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no horizontal scroll');
assert.match(await nav('Review').innerText(), /5/, 'Review shows how many items wait');
await nav('Review').click();
await panel.getByRole('link', { name: 'u/amy' }).waitFor();
const inbox = await panel.locator('main').innerText();
const fieldValues = await panel.locator('main textarea, main input').evaluateAll((els) => els.map((e) => e.value));
for (const t of ['u/amy', 'u/bob', 'comment draft', 'warm comment', 'Post title']) assert.ok(inbox.includes(t) || fieldValues.includes(t), 'Waiting inbox shows ' + t);
assert.ok(!inbox.includes('u/cat'), 'sent leads are not in Waiting');
await shots('review');
await panel.locator('.seg.filter').getByRole('button', { name: /^DMs/ }).click();
await panel.getByRole('link', { name: 'u/cat' }).waitFor(); // DMs filter shows every lead, sent ones included
await panel.reload(); await panel.waitForSelector('nav.tabs');
assert.equal(await nav('Review').getAttribute('aria-current'), 'page', 'last tab remembered');
await nav('Setup').click();
await panel.getByRole('heading', { name: 'Template' }).waitFor();
await panel.getByRole('heading', { name: /Your product/ }).waitFor();
await shots('setup');
await panel.getByRole('button', { name: 'Settings' }).click();
await panel.getByRole('heading', { name: 'DeepSeek' }).waitFor();
lap('5 sections: Home, Review (DMs + comments + posts), Setup (incl. template), Warm-up, Settings gear; fits 360px; tab remembered');

// ============ P3-3 warm-up vocabulary + plan controls scheduling
assert.ok(await panel.getByText('DM ramp (new accounts)').isVisible());
assert.equal(await panel.getByText(/Controlled by your warm-up plan/).count(), 0);
await sw.evaluate(async (plan) => chrome.storage.local.set({ warmup: { enabled: true, startedAt: Date.now(), docs: [], plan, approval: 'review' } }), PLAN);
await panel.getByText(/Controlled by your warm-up plan/).first().waitFor();
assert.equal(await panel.getByLabel(/DM ramp \(new accounts\)/).isDisabled(), true, 'ramp greyed out while the plan runs');
assert.equal(await panel.getByLabel('Run sessions automatically').isDisabled(), true);
assert.equal(await panel.getByRole('button', { name: /Restart DM ramp/ }).count(), 0, 'one restart control: the plan has its own');
await shots('settings-plan');
await nav('Home').click();
assert.ok(await panel.getByRole('button', { name: 'Warm-up plan' }).isVisible());
lap('"DM ramp (new accounts)" vs "Warm-up plan"; plan greys out Settings scheduling and ramp');

// ============ P3-5 settings validation, Test key, Setup inline status, leads search/sort/undo
await full();
await open(); await panel.getByRole('button', { name: 'Settings' }).click();
const cap = panel.getByLabel('Max DMs per day');
await cap.fill('');
await panel.waitForTimeout(400);
assert.equal((await get('settings')).dailyDmCap, 40, 'clearing the box while typing does not write 0');
await cap.fill('25'); await panel.waitForTimeout(400);
assert.equal((await get('settings')).dailyDmCap, 25);
await panel.getByLabel('Min gap between DMs (s)').fill('300'); await panel.waitForTimeout(400);
await panel.getByText(/at least the min gap/i).waitFor();
assert.equal((await get('settings')).minGapSec, 60, 'min gap above max gap is not saved');
await panel.getByLabel('Active from (hour)').fill('30'); await panel.waitForTimeout(300);
await panel.getByText('Use an hour between 0 and 23.').waitFor();
assert.equal((await get('settings')).activeStart, 9);
await panel.getByRole('button', { name: 'Test key' }).click();
await panel.getByText('Key works. DeepSeek answered.').waitFor();
await shots('settings-errors');
lap('settings: no 0 while typing, min/max gap and hours validated inline, Test key');

await seed({ settings: { ...SETTINGS, deepseekKey: '' } });
await open(); await nav('Setup').click();
await panel.getByLabel('Website').fill('example.com');
await panel.getByRole('button', { name: 'Analyze my product' }).click();
const an = panel.locator('.card', { hasText: 'Your product' }).locator('.notice');
await an.waitFor();
assert.match(await an.innerText(), /Add your DeepSeek API key first/);
lap('Setup: Analyze checks the key first and says so under the button (no permission prompt)');

await full({ 'lead:amy': lead('amy', 'draft', 72, 3), 'lead:bob': lead('bob', 'draft', 90, 1), 'lead:dan': lead('dan', 'queued', 55, 4, '') });
await open(); await nav('Review').click(); await panel.locator('.seg.filter').getByRole('button', { name: /^DMs/ }).click();
await panel.getByRole('link', { name: 'u/dan' }).waitFor();
assert.match(await panel.locator('.showing').innerText(), /Showing 3 of 3/);
const names = async () => (await panel.locator('.item a.t').allInnerTexts());
assert.deepEqual(await names(), ['u/bob', 'u/amy', 'u/dan'], 'sorted by fit');
await panel.getByLabel('Sort').selectOption('newest');
assert.deepEqual(await names(), ['u/dan', 'u/amy', 'u/bob']);
await panel.getByLabel('Search leads').fill('amy');
assert.deepEqual(await names(), ['u/amy']); assert.match(await panel.locator('.showing').innerText(), /Showing 1 of 1/);
assert.ok(await panel.locator('.pill', { hasText: 'Fit 72' }).isVisible() && await panel.locator('.pill', { hasText: 'Waiting for approval' }).isVisible(), 'labelled fit + friendly status');
await panel.getByRole('button', { name: 'Skip' }).click();
await panel.locator('.toast').waitFor();
assert.equal((await get('lead:amy')).status, 'skipped');
await panel.locator('.toast').getByRole('button', { name: 'Undo' }).click();
await panel.waitForTimeout(300);
assert.equal((await get('lead:amy')).status, 'draft', 'undo restores the previous status');
await panel.getByRole('button', { name: 'Skip' }).click();
await panel.getByLabel('Search leads').fill('');
await panel.getByLabel('Status').selectOption('skipped');
await panel.getByRole('button', { name: 'Restore' }).click(); await panel.waitForTimeout(300);
assert.equal((await get('lead:amy')).status, 'draft', 'Restore in the Skipped filter');
lap('leads: search, sort, "Showing N of M", labelled fit, friendly status, Skip has Undo + Restore');

// ============ P3-4 design system checks in the real page
await full({ 'lead:amy': lead('amy', 'draft', 72, 3) });
await open();
const tiny = await panel.evaluate(() => [...document.querySelectorAll('body *')].filter((e) => e.childNodes.length && [...e.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim()) && parseFloat(getComputedStyle(e).fontSize) < 11).map((e) => e.tagName + ':' + e.textContent.slice(0, 20)));
assert.deepEqual(tiny, [], 'no visible text under 11px');
await panel.keyboard.press('Tab'); await panel.keyboard.press('Tab');
const focus = await panel.evaluate(() => { const e = document.activeElement; const s = getComputedStyle(e); return { tag: e.tagName, outline: s.outlineStyle, w: s.outlineWidth }; });
assert.ok(focus.outline !== 'none' && focus.w !== '0px', 'keyboard focus is visible: ' + JSON.stringify(focus));
for (const id of ['review', 'warmup', 'setup']) {
  await nav({ review: 'Review', warmup: 'Warm-up', setup: 'Setup' }[id]).click();
  const small = await panel.evaluate(() => [...document.querySelectorAll('button, .btn')].filter((b) => b.offsetParent && b.getBoundingClientRect().height < 28).map((b) => b.textContent.trim()));
  assert.deepEqual(small, [], `${id}: every button is at least 28px tall`);
}
await nav('Warm-up').click();
const file = panel.locator('[data-testid=sop-file]');
assert.notEqual(await file.evaluate((e) => getComputedStyle(e).display), 'none', 'file input is not display:none');
await file.focus(); assert.equal(await panel.evaluate(() => document.activeElement?.getAttribute('data-testid')), 'sop-file', 'file input is keyboard reachable');
await shots('warmup');
await panel.emulateMedia({ colorScheme: 'dark' });
const bg = await panel.evaluate(() => getComputedStyle(document.body).backgroundColor);
assert.equal(bg, 'rgb(11, 17, 32)', 'dark mode uses the dark tokens');
await panel.emulateMedia({ colorScheme: 'light' });
await nav('Home').click();
await shots('home');
lap('design: type >= 11px, visible focus, 28px targets, keyboard-reachable file input, dark mode');

// ============ P3-6 HUD: top-centre (off Reddit chat), Stop labelled, Esc stops
await sw.evaluate(() => { globalThis.__stops = 0; chrome.runtime.onMessage.addListener((m) => { if (m?.type === 'user-stop') globalThis.__stops++; }); });
const rp = await ctx.newPage();
await rp.goto('https://www.reddit.com/r/saas/');
const tabCmd = (c) => sw.evaluate(async (c) => { const [t] = await chrome.tabs.query({ url: 'https://www.reddit.com/*' }); return chrome.tabs.sendMessage(t.id, { target: 'page', ...c }); }, c);
for (let i = 0; i < 40; i++) { try { if ((await tabCmd({ cmd: 'ping' }))?.ok) break; } catch {} await rp.waitForTimeout(250); }
await tabCmd({ cmd: 'hud', text: 'Browsing <b>r/saas</b>' });
if (SHOTS) await rp.screenshot({ path: path.join(SHOTS, 'p3-hud.png') });
await rp.bringToFront();
await rp.keyboard.press('Escape');
for (let i = 0; i < 20 && !(await sw.evaluate(() => globalThis.__stops)); i++) await rp.waitForTimeout(150);
assert.equal(await sw.evaluate(() => globalThis.__stops), 1, 'Esc sends Stop');
await tabCmd({ cmd: 'hud', text: '' });
await rp.keyboard.press('Escape'); await rp.waitForTimeout(300);
assert.equal(await sw.evaluate(() => globalThis.__stops), 1, 'Esc does nothing when the agent is not running');
lap('HUD: Esc stops while it is shown, ignored otherwise');

await ctx.close();
console.log('\nPHASE 3 E2E PASSED');
