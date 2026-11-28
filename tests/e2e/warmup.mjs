// SOP warm-up, end to end in real Chromium with the built extension: upload (txt/docx/pdf/png -> real OCR) -> build plan -> run sessions.
// Reddit and the LLM are mocked (mechanics only). Run `npm run build` first.
import { chromium } from 'playwright';
import { pages } from './mock.mjs';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import zlib from 'node:zlib';

const EXT = new URL('../../.output/chrome-mv3', import.meta.url).pathname;
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'pigeon-sop-'));
const ctx = await chromium.launchPersistentContext(fs.mkdtempSync(path.join(os.tmpdir(), 'pw-ext-')), { channel: 'chromium', headless: true, viewport: { width: 460, height: 1000 }, args: [`--disable-extensions-except=${EXT}`, `--load-extension=${EXT}`] });
let [sw] = ctx.serviceWorkers(); if (!sw) sw = await ctx.waitForEvent('serviceworker');
const extId = new URL(sw.url()).host;
const t0 = Date.now(); const lap = (m) => console.log(`✓ ${m} (${((Date.now() - t0) / 1000).toFixed(0)}s)`);

// ---------------------------------------------------------------- fixtures: the same SOP in four formats
const SOP = [
  'Reddit account warm-up SOP',
  'Days 1-3: only read and upvote. Upvote 3 posts a day. Join 1 subreddit a day. No comments, no posts, no DMs.',
  'Days 4-10: comment once a day, 1 post total. Never include links. Karma must be 50 before commenting.',
  'Subreddits: r/saas (comment from day 4), r/startups (read only).',
  'Verify your email and set an avatar before starting.',
].join('\n');
const sopTxt = path.join(tmp, 'sop.txt'); fs.writeFileSync(sopTxt, SOP);

function makeZip(files) {
  const parts = [], central = []; let offset = 0;
  for (const [name, content] of Object.entries(files)) {
    const nb = Buffer.from(name), raw = Buffer.from(content), data = zlib.deflateRawSync(raw);
    const lh = Buffer.alloc(30); lh.writeUInt32LE(0x04034b50, 0); lh.writeUInt16LE(20, 4); lh.writeUInt16LE(8, 8); lh.writeUInt32LE(data.length, 18); lh.writeUInt32LE(raw.length, 22); lh.writeUInt16LE(nb.length, 26);
    const ch = Buffer.alloc(46); ch.writeUInt32LE(0x02014b50, 0); ch.writeUInt16LE(20, 4); ch.writeUInt16LE(20, 6); ch.writeUInt16LE(8, 10); ch.writeUInt32LE(data.length, 20); ch.writeUInt32LE(raw.length, 24); ch.writeUInt16LE(nb.length, 28); ch.writeUInt32LE(offset, 42);
    parts.push(lh, nb, data); central.push(ch, nb); offset += 30 + nb.length + data.length;
  }
  const cd = Buffer.concat(central), e = Buffer.alloc(22);
  e.writeUInt32LE(0x06054b50, 0); e.writeUInt16LE(Object.keys(files).length, 8); e.writeUInt16LE(Object.keys(files).length, 10); e.writeUInt32LE(cd.length, 12); e.writeUInt32LE(offset, 16);
  return Buffer.concat([...parts, cd, e]);
}
const docxPath = path.join(tmp, 'sop.docx');
fs.writeFileSync(docxPath, makeZip({ 'word/document.xml': `<w:document><w:body>${SOP.split('\n').map((l) => `<w:p><w:r><w:t>${l.replace(/&/g, '&amp;')}</w:t></w:r></w:p>`).join('')}</w:body></w:document>` }));

const html = `<html><body style="font:28px Arial;padding:30px;width:1100px">${SOP.split('\n').map((l) => `<p>${l}</p>`).join('')}</body></html>`;
const rp = await ctx.newPage(); await rp.setContent(html);
const pdfPath = path.join(tmp, 'sop.pdf'); await rp.pdf({ path: pdfPath, format: 'A4' });
const pngPath = path.join(tmp, 'sop-photo.png'); await rp.setViewportSize({ width: 1200, height: 500 }); await rp.screenshot({ path: pngPath });
await rp.close();
const junk = path.join(tmp, 'notes.doc'); fs.writeFileSync(junk, 'binary');

// ---------------------------------------------------------------- mocks
const PLAN = (over = {}) => ({
  name: 'Mock SOP plan', summary: 'Read first, then comment.',
  phases: [{ name: 'Engage', fromDay: 1, toDay: 10, goal: 'comment helpfully', quota: { browse: 1, upvote: 3, comment: 1, post: 1, join: 1, dm: 0 }, notes: '' }],
  subreddits: [{ name: 'saas', fromDay: 1, actions: [], notes: 'Helpful, no links' }],
  voice: 'casual and brief', rules: ['never include links'], avoid: [], sessionsPerDay: 2,
  manualSteps: ['Verify your email and set an avatar'], assumptions: ['Assumed 1 post total means 1 a day'], warnings: [], ...over,
});
const seen = { compile: [], writes: 0 };
let planOverride = null;
await ctx.route('https://api.deepseek.com/**', async (route) => {
  const body = JSON.parse(route.request().postData());
  const sys = body.messages[0].content, usr = body.messages[1].content;
  let out = {};
  if (sys.includes('machine-readable schedule')) { seen.compile.push(usr); out = PLAN(planOverride ?? {}); }
  else if (sys.includes('choose which Reddit posts')) {
    const ids = [...usr.matchAll(/\[id=([^\]]+)\]/g)].map((m) => m[1]);
    out = { upvote: ids.slice(0, 2), comment: ids.slice(0, 1) };
  } else if (sys.includes('write ONE Reddit comment')) { seen.writes++; out = { comment: 'Mock helpful reply: try asking where your buyers already complain about alternatives.', why: 'they asked directly' }; }
  else if (sys.includes('write ONE Reddit text post')) { seen.writes++; out = { title: 'What I learned talking to 20 founders', body: 'Mock post body about lessons learned from customer conversations, no links.', why: 'fits the sub' }; }
  await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ choices: [{ message: { content: JSON.stringify(out) } }] }) });
});
const joinBtn = `<button id="join">Join</button><script>document.getElementById('join').onclick=e=>{e.target.textContent='Joined'}</script>`;
const submitPage = `<!doctype html><body><h1>Create post</h1><textarea placeholder="Title" id="t"></textarea><div contenteditable="true" aria-label="Body text" id="b"></div>
  <button id="post" onclick="location.href='/r/saas/comments/new1/mock_post/'">Post</button></body>`;
await ctx.route('https://www.reddit.com/**', (route) => {
  const u = new URL(route.request().url());
  if (u.pathname === '/api/me.json' || /about\.json$/.test(u.pathname) && u.pathname.startsWith('/user/')) return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ data: { total_karma: 120 } }) });
  if (u.pathname.endsWith('rules.json')) return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ rules: [{ short_name: 'Be nice', description: 'no spam' }] }) });
  const p = u.pathname;
  const body = p.includes('/submit') ? submitPage : p.startsWith('/user/') ? pages.profile(p.split('/')[2]) : p.includes('/comments/') ? pages.postPage() : pages.listing().replace('</header>', '</header>' + joinBtn);
  route.fulfill({ contentType: 'text/html', body });
});

const get = (k) => sw.evaluate(async (k) => (await chrome.storage.local.get(k))[k], k);
const seed = (settings = {}, warmup = {}, extra = {}) => sw.evaluate(async ([settings, warmup, extra]) => {
  await chrome.storage.local.clear();
  await chrome.storage.local.set({
    settings: { deepseekKey: 'sk-test', model: 'deepseek-flash', dailyDmCap: 40, warmup: true, warmupStart: Date.now(), activeStart: 0, activeEnd: 24, minGapSec: 60, maxGapSec: 90, minFit: 70, sessionMinutes: 0.05, upvotes: true, maxUpvotesPerSession: 5, dmMode: 'template', approveAiDrafts: true, aiGuidance: '', autoSchedule: false, sessionsPerDay: 3, myUsername: 'me_tester', pace: 1.8, ...settings },
    warmup: { enabled: false, startedAt: Date.now(), docs: [], plan: null, approval: 'review', ...warmup },
    ...extra,
  });
}, [settings, warmup, extra]);
const waitIdle = async (ms) => { const end = Date.now() + ms; await new Promise((r) => setTimeout(r, 1500)); while (Date.now() < end) { const a = await get('agent'); if (a && a.running === false && a.phase === 'idle') return a; await new Promise((r) => setTimeout(r, 1000)); } throw new Error('never finished; log=' + JSON.stringify((await get('log')).slice(-8))); };
const msgs = async () => (await get('log') ?? []).map((l) => l.msg);

// ================================================================ 1. uploads through the real panel
await seed();
const panel = await ctx.newPage(); await panel.goto(`chrome-extension://${extId}/sidepanel.html`);
await panel.locator('nav.tabs').getByRole('button', { name: 'Warm-up', exact: true }).click();
await panel.setInputFiles('[data-testid=sop-file]', [sopTxt, docxPath, pdfPath, junk]);
await panel.waitForFunction(() => document.body.innerText.includes("files can't be read"), null, { timeout: 60000 });
let docs = (await get('warmup')).docs;
assert.deepEqual(docs.map((d) => [d.name, d.via]).sort(), [['sop.docx', 'docx'], ['sop.pdf', 'pdf'], ['sop.txt', 'text']], JSON.stringify(docs.map((d) => [d.name, d.via])));
for (const d of docs) assert.ok(/Upvote 3 posts a day/.test(d.text.replace(/\s+/g, ' ')) && /Verify your email/.test(d.text.replace(/\s+/g, ' ')), `${d.name} text: ${d.text.slice(0, 200)}`);
assert.match(await panel.locator('body').innerText(), /\.doc files can't be read/, 'unsupported legacy .doc gives a clear message');
lap('txt, docx and a real PDF were read; legacy .doc rejected with a reason');

await panel.setInputFiles('[data-testid=sop-file]', [pngPath]);
for (let i = 0; i < 240 && !(await get('warmup')).docs.some((d) => d.name === 'sop-photo.png') && !/Couldn't find readable/.test(await panel.locator('body').innerText()); i++) await new Promise((r) => setTimeout(r, 500));
docs = (await get('warmup')).docs;
const photo = docs.find((d) => d.name === 'sop-photo.png');
assert.ok(photo && photo.via === 'image-ocr', JSON.stringify(photo));
const flat = photo.text.toLowerCase().replace(/\s+/g, ' ');
for (const w of ['warm-up', 'upvote', 'comment', 'subreddits', 'email']) assert.ok(flat.includes(w), `OCR missing "${w}": ${flat.slice(0, 300)}`);
lap(`PNG screenshot read by the bundled OCR: "${flat.slice(0, 70)}…"`);

// ================================================================ 2. build the plan
await panel.getByRole('button', { name: /Build my warm-up plan/ }).click();
await panel.waitForFunction(() => document.body.innerText.includes('Mock SOP plan'), null, { timeout: 30000 });
const w1 = await get('warmup');
assert.equal(w1.plan.phases[0].quota.upvote, 3);
assert.ok(seen.compile[0].includes('===== DOCUMENT: sop.docx') && seen.compile[0].includes('sop-photo.png') && seen.compile[0].includes('Upvote 3 posts a day'), 'every uploaded document reached the model');
assert.match(await panel.locator('body').innerText(), /Do these yourself[\s\S]*Verify your email/, 'steps Pigeon cannot do are shown as manual');
assert.match(await panel.locator('body').innerText(), /What Pigeon had to assume/, 'assumptions are shown');
lap('plan built from all 4 documents; manual steps + assumptions surfaced');
await panel.close();

// ================================================================ 3. a session, approval mode
await seed({}, { plan: PLAN(), approval: 'review' }, { product: { url: 'https://x.com', name: 'Pigeon', summary: 'finds customers on reddit', offer: 'x', icp: 'founders', personas: [], painPhrases: [], buyingSignals: [], competitors: [], subredditIdeas: [], searchQueries: [] } });
const sp = await ctx.newPage(); await sp.goto(`chrome-extension://${extId}/sidepanel.html`);
const send = (m) => sp.evaluate((m) => chrome.runtime.sendMessage(m), m);
let r = await send({ type: 'agent:start', mode: 'warmup' }); assert.ok(r.ok, JSON.stringify(r));
await waitIdle(420000);
let log = await msgs(); console.log('--- log ---\n' + log.map((l) => '  ' + l).join('\n') + '\n-----------');
let wd = await get('warmupDaily');
assert.equal(wd.phase, 'Engage');
assert.ok(wd.done.upvote >= 1, 'upvoted: ' + JSON.stringify(wd.done));
assert.equal(wd.done.join, 1); assert.deepEqual(await get('warmupJoined'), ['saas']);
assert.ok(wd.done.browse > 0);
assert.equal(wd.done.comment, 0, 'review mode: nothing posted without approval');
let items = await get('warmupItems');
assert.deepEqual(items.map((i) => [i.kind, i.status]).sort(), [['comment', 'pending'], ['post', 'pending']], JSON.stringify(items));
assert.ok(items.find((i) => i.kind === 'comment').body.startsWith('Mock helpful reply'));
assert.ok(log.some((l) => /Joined r\/saas/.test(l)) && log.some((l) => /Comment drafted for approval/.test(l)));
lap(`session: joined r/saas, read it, upvoted ${wd.done.upvote}, drafted 1 comment + 1 post and held both for approval`);

// approve both from the UI path
const cid = items.find((i) => i.kind === 'comment').id, pid = items.find((i) => i.kind === 'post').id;
r = await send({ type: 'warmup:post', id: cid }); assert.ok(r.ok, JSON.stringify(r));
r = await send({ type: 'warmup:post', id: pid }); assert.ok(r.ok, JSON.stringify(r));
items = await get('warmupItems'); wd = await get('warmupDaily');
assert.deepEqual(items.map((i) => i.status), ['posted', 'posted']);
assert.equal(wd.done.comment, 1); assert.equal(wd.done.post, 1);
lap('approving posted the comment and the text post (redirect to the new thread detected); quotas booked');

// ================================================================ 4. quota is spent: a second session does no more drafting
await send({ type: 'agent:start', mode: 'warmup' }); await waitIdle(300000);
log = await msgs();
assert.ok(log.some((l) => /Today's warm-up is done|Warm-up day/.test(l)));
assert.equal((await get('warmupItems')).length, 2, 'no extra drafts once the day is done');
lap("second session: today's quota already met, nothing more drafted");

// ================================================================ 5. auto mode posts by itself
seen.writes = 0;
await seed({}, { plan: PLAN({ phases: [{ name: 'Engage', fromDay: 1, toDay: 10, goal: '', quota: { browse: 1, upvote: 1, comment: 1, post: 0, join: 0, dm: 0 }, notes: '' }] }), approval: 'auto' });
await send({ type: 'agent:start', mode: 'warmup' }); await waitIdle(420000);
items = await get('warmupItems'); wd = await get('warmupDaily');
assert.deepEqual(items.map((i) => [i.kind, i.status]), [['comment', 'posted']], JSON.stringify(items));
assert.equal(wd.done.comment, 1);
lap('auto mode: the comment was posted without a click');

// ================================================================ 6. plan DM allowance gates the DM queue; karma gate; finished plan
const lead = { username: 'someone', subreddit: 'saas', postUrl: '', postTitle: 't', snippet: '', fit: 99, intent: 'x', reason: 'r', status: 'ready', message: 'hello there, quick question for you', source: 'csv', createdAt: Date.now() };
await seed({}, { enabled: true, plan: PLAN({ phases: [{ name: 'Lurk', fromDay: 1, toDay: 5, goal: 'read', quota: { browse: 5, upvote: 1, comment: 0, post: 0, join: 0, dm: 0 }, notes: '' }] }) }, { 'lead:someone': lead });
await send({ type: 'agent:start', mode: 'dm' }); await waitIdle(60000);
assert.equal((await get('lead:someone')).status, 'ready', 'DM must not go out in a phase whose DM allowance is 0');
assert.ok((await msgs()).some((l) => /Warm-up plan: no DMs in "Lurk"/.test(l)), JSON.stringify(await msgs()));
lap('DM held because the SOP allows 0 DMs in the current phase');

await seed({}, { enabled: true, plan: PLAN({ phases: [{ name: 'Lurk', fromDay: 1, toDay: 5, goal: '', quota: { browse: 1, upvote: 1, comment: 0, post: 0, join: 0, dm: 0 }, notes: '' }, { name: 'Engage', fromDay: 6, toDay: 9, goal: '', minKarma: 500, quota: { browse: 1, upvote: 1, comment: 2, post: 0, join: 0, dm: 0 }, notes: '' }] }), startedAt: Date.now() - 7 * 86400000 });
await send({ type: 'agent:start', mode: 'warmup' }); await waitIdle(420000);
log = await msgs();
assert.ok(log.some((l) => /Held in "Lurk" until 500 karma \(account has 120\)/.test(l)), JSON.stringify(log));
assert.equal((await get('warmupItems') ?? []).length, 0, 'held in the Lurk phase: no comments drafted');
lap('karma gate: account (120) is below the 500 the SOP requires, so it stayed in the read-only phase');

await seed({}, { plan: PLAN(), startedAt: Date.now() - 30 * 86400000 });
r = await send({ type: 'agent:start', mode: 'warmup' }); await new Promise((r) => setTimeout(r, 1500)); await waitIdle(30000);
assert.ok((await msgs()).some((l) => /plan is finished/.test(l)), JSON.stringify(await msgs()));
lap('a finished plan refuses to run and says why');

await ctx.close();
console.log('\nWARM-UP E2E PASSED');
