// Proves behaviour varies: typing rhythm, typos corrected, press durations, scroll profile. Mock Reddit DOM.
import { chromium } from 'playwright';
import { pages } from './mock.mjs';
import assert from 'node:assert/strict';
import fs from 'node:fs';
const EXT = new URL('../../.output/chrome-mv3', import.meta.url).pathname;
const ctx = await chromium.launchPersistentContext(fs.mkdtempSync('/tmp/pw-ext-'), { channel: 'chromium', headless: true, args: [`--disable-extensions-except=${EXT}`, `--load-extension=${EXT}`] });
let [sw] = ctx.serviceWorkers(); if (!sw) sw = await ctx.waitForEvent('serviceworker');
await ctx.route('https://www.reddit.com/**', (route) => {
  const u = new URL(route.request().url());
  const html = u.pathname.startsWith('/user/') ? pages.profile(u.pathname.split('/')[2]) : u.pathname.includes('/comments/') ? pages.postPage() : pages.listing();
  route.fulfill({ contentType: 'text/html', body: html });
});
const page = await ctx.newPage();
let ev = [];
await page.exposeFunction('rec', (e) => ev.push(e));
await page.addInitScript(() => {
  const r = (t, e) => window.rec?.({ t, k: e.key, ts: performance.now(), y: window.scrollY, x: e.clientX, yy: e.clientY, unsafe: !!(e.target?.closest?.('a,button,input,textarea,shreddit-post,shreddit-comment,[role=button]')) });
  addEventListener('keydown', (e) => r('kd', e), true);
  addEventListener('mousedown', (e) => r('md', e), true);
  addEventListener('mouseup', (e) => r('mu', e), true);
  addEventListener('mousemove', (e) => r('mm', e), true);
  addEventListener('scroll', (e) => r('sc', e), true);
});
const tab = () => sw.evaluate(async () => (await chrome.tabs.query({ url: 'https://www.reddit.com/*' }))[0].id);
const cmd = async (c) => sw.evaluate(([id, c]) => chrome.tabs.sendMessage(id, { target: 'page', ...c }), [await tab(), c]);
const ready = async () => { for (let i = 0; i < 40; i++) { try { if ((await cmd({ cmd: 'ping' }))?.ok) return; } catch {} await new Promise((r) => setTimeout(r, 250)); } throw new Error('not ready'); };
const persona = (o) => ({ id: 'x', born: Date.now(), tempo: 1, typingCps: 4.3, typoRate: 0.01, scrollSpeed: 1, scrollChunky: false, mouseSpeed: 1, patience: 1, hesitation: 0.1, overshoot: 0.3, ...o });
const withPersona = async (url, p) => { await page.goto(url); await page.evaluate((p) => sessionStorage.setItem('pigeon:persona', JSON.stringify(p)), p); await page.reload(); await ready(); ev = []; };
const stdev = (a) => { const m = a.reduce((x, y) => x + y, 0) / a.length; return Math.sqrt(a.reduce((x, y) => x + (y - m) ** 2, 0) / a.length); };
const mean = (a) => a.reduce((x, y) => x + y, 0) / a.length;
const MSG = "Hey alex_dev, saw your post about finding first customers. I build a tool for exactly that, curious how you're handling it today?";

// ---- typing: typo-prone persona. Final text must be exact; backspaces prove typos happened and were fixed.
await withPersona('https://www.reddit.com/user/alex_dev/', persona({ typoRate: 0.22, typingCps: 7 }));
let t0 = Date.now(); let r = await cmd({ cmd: 'dm', text: MSG }); const fastMs = Date.now() - t0;
assert.ok(r.ok, JSON.stringify(r));
assert.deepEqual(await page.evaluate(() => window.__log.dms), [MSG], 'final text is exactly the intended message despite typos');
const keys = ev.filter((e) => e.t === 'kd');
const backspaces = keys.filter((e) => e.k === 'Backspace').length;
assert.ok(backspaces >= 3, 'typos were made and corrected: ' + backspaces + ' backspaces');
const gaps = keys.slice(1).map((e, i) => e.ts - keys[i].ts);
const cv = stdev(gaps) / mean(gaps);
assert.ok(cv > 0.35, 'key intervals vary (CV=' + cv.toFixed(2) + ')');
assert.ok(new Set(gaps.map((g) => Math.round(g / 5))).size > 25, 'many distinct intervals');
console.log(`✓ typing: ${keys.length} keystrokes, ${backspaces} corrected typos, interval CV=${cv.toFixed(2)}, final text exact (${(fastMs / 1000).toFixed(0)}s)`);

// speed swings mid-message: rolling-window mean interval varies a lot
const win = []; for (let i = 0; i + 20 <= gaps.length; i += 10) win.push(mean(gaps.slice(i, i + 20)));
const swing = Math.max(...win) / Math.min(...win);
assert.ok(swing > 1.5, 'typing speed swings during the message (fastest vs slowest 20-key window x' + swing.toFixed(2) + ')');
console.log(`✓ speed varies mid-writing: fastest vs slowest stretch differ x${swing.toFixed(2)}`);
const kinds1 = (await cmd({ cmd: 'typoLog' })).log;

// ---- same message, fast vs slow persona (typos off for both) => visibly different, still exact
await withPersona('https://www.reddit.com/user/alex_dev/', persona({ typoRate: 0, typingCps: 7 }));
t0 = Date.now(); r = await cmd({ cmd: 'dm', text: MSG }); const fastClean = Date.now() - t0;
assert.ok(r.ok && (await page.evaluate(() => window.__log.dms))[0] === MSG);
await withPersona('https://www.reddit.com/user/alex_dev/', persona({ typoRate: 0, typingCps: 2.4, tempo: 1.3 }));
t0 = Date.now(); r = await cmd({ cmd: 'dm', text: MSG }); const slowMs = Date.now() - t0;
assert.ok(r.ok && (await page.evaluate(() => window.__log.dms))[0] === MSG);
assert.ok(slowMs > fastClean * 1.4, `slow persona is slower: ${slowMs} vs ${fastClean}`);
console.log(`✓ personas differ: fast ${(fastClean / 1000).toFixed(0)}s vs slow ${(slowMs / 1000).toFixed(0)}s for the same DM`);

await withPersona('https://www.reddit.com/user/alex_dev/', persona({ typoRate: 0.25, typingCps: 9, tempo: 0.8 }));
await cmd({ cmd: 'dm', text: MSG });
assert.deepEqual(await page.evaluate(() => window.__log.dms), [MSG]);
const kinds = new Set([...kinds1, ...(await cmd({ cmd: 'typoLog' })).log]);
assert.ok(kinds.size >= 3, 'several kinds of typo: ' + [...kinds]);
console.log(`✓ typo kinds seen: ${[...kinds].join(', ')} (all corrected, text exact)`);

// ---- clicks: press durations + landing points vary (chat button, composer, send = 3 clicks per DM)
const presses = [], pts = [];
for (let n = 0; n < 4; n++) {
  await withPersona('https://www.reddit.com/user/alex_dev/', persona({ typoRate: 0, typingCps: 9, tempo: 0.8 }));
  await cmd({ cmd: 'dm', text: 'quick hello there friend, short test message' });
  const d = ev.filter((e) => e.t === 'md'), u = ev.filter((e) => e.t === 'mu');
  d.forEach((e, i) => { if (u[i]) presses.push(u[i].ts - e.ts); pts.push([e.x, e.yy]); });
}
assert.ok(presses.length >= 10);
assert.ok(stdev(presses) > 12 && Math.min(...presses) >= 30 && Math.max(...presses) <= 270, `press durations vary ${Math.min(...presses).toFixed(0)}–${Math.max(...presses).toFixed(0)}ms`);
assert.ok(new Set(presses.map((p) => Math.round(p / 4))).size >= presses.length * 0.7, 'press durations rarely repeat');
// the same button (first click of each DM, "Chat") is hit at a different pixel each time
const first = pts.filter((_, i) => i % 3 === 0).map((p) => `${p[0].toFixed(0)},${p[1].toFixed(0)}`);
assert.equal(new Set(first).size, first.length, 'same button clicked at different points: ' + first.join(' | '));
console.log(`✓ clicks: ${presses.length} presses ${Math.min(...presses).toFixed(0)}–${Math.max(...presses).toFixed(0)}ms (sd ${stdev(presses).toFixed(0)}), same button hit at ${first.length} different points`);

// ---- scroll: two personas scroll differently; pauses between scroll bursts vary
const profile = async (p) => {
  await withPersona('https://www.reddit.com/r/saas/', p);
  await page.evaluate(() => window.scrollTo(0, 0)); ev = [];
  await cmd({ cmd: 'browse', maxPosts: 50, seconds: 14 });
  const sc = ev.filter((e) => e.t === 'sc');
  const steps = sc.slice(1).map((e, i) => Math.abs(e.y - sc[i].y)).filter((d) => d > 0);
  const idle = sc.slice(1).map((e, i) => e.ts - sc[i].ts).filter((g) => g > 120);
  return { n: sc.length, medStep: steps.sort((a, b) => a - b)[Math.floor(steps.length / 2)], idle };
};
const chunky = await profile(persona({ scrollChunky: true }));
const glide = await profile(persona({ scrollChunky: false }));
assert.ok(chunky.n > 20 && glide.n > 20);
assert.ok(chunky.idle.length >= 4 && stdev(chunky.idle) / mean(chunky.idle) > 0.3, 'pauses between scroll bursts vary (chunky)');
assert.ok(glide.idle.length >= 4 && stdev(glide.idle) / mean(glide.idle) > 0.3, 'pauses between scroll bursts vary (glide)');
assert.notEqual(chunky.medStep, glide.medStep);
console.log(`✓ scrolling: wheel-tick persona median step ${chunky.medStep.toFixed(0)}px vs trackpad-glide ${glide.medStep.toFixed(0)}px; pause CV ${(stdev(chunky.idle) / mean(chunky.idle)).toFixed(2)}/${(stdev(glide.idle) / mean(glide.idle)).toFixed(2)}`);

// ---- cursor paths are irregular and different every time
await withPersona('https://www.reddit.com/r/saas/', persona({ overshoot: 0, hesitation: 0 }));
await cmd({ cmd: 'moveCursor', x: 80, y: 120 }); // known start
const A = [80, 120], B = [900, 640];
const runs = [];
for (let n = 0; n < 8; n++) {
  await cmd({ cmd: 'moveCursor', x: A[0], y: A[1] }); ev = [];
  await cmd({ cmd: 'moveCursor', x: B[0], y: B[1] });
  const pts = ev.filter((e) => e.t === 'mm').map((e) => [e.x, e.yy]);
  if (pts.length >= 5) runs.push(pts);
}
assert.ok(runs.length >= 6, 'recorded paths');
const len = (p) => p.slice(1).reduce((a, q, i) => a + Math.hypot(q[0] - p[i][0], q[1] - p[i][1]), 0);
const straight = Math.hypot(B[0] - A[0], B[1] - A[1]);
const nrm = [-(B[1] - A[1]) / straight, (B[0] - A[0]) / straight];
const lateral = (p) => p.map((q) => (q[0] - A[0]) * nrm[0] + (q[1] - A[1]) * nrm[1]);
const maxLat = runs.map((p) => lateral(p).reduce((a, b) => (Math.abs(b) > Math.abs(a) ? b : a), 0));
const wiggles = runs.map((p) => { const l = lateral(p); let c = 0; for (let i = 2; i < l.length; i++) { const d1 = l[i - 1] - l[i - 2], d2 = l[i] - l[i - 1]; if (d1 * d2 < 0 && Math.abs(d1) + Math.abs(d2) > 0.8) c++; } return c; });
const ratio = runs.map((p) => len(p) / straight);
assert.ok(mean(ratio) > 1.03, 'paths are not straight lines (length/straight=' + mean(ratio).toFixed(3) + ')');
assert.ok(stdev(maxLat) > 10, 'each path bows differently (max lateral offset sd ' + stdev(maxLat).toFixed(0) + 'px: ' + maxLat.map((v) => v.toFixed(0)).join(',') + ')');
assert.ok(wiggles.filter((w) => w >= 1).length >= runs.length / 2, 'paths wander/change direction: ' + wiggles.join(','));
console.log(`✓ cursor paths: ${runs.length} runs A→B, length ${mean(ratio).toFixed(2)}× the straight line, lateral bow ${Math.min(...maxLat).toFixed(0)}…${Math.max(...maxLat).toFixed(0)}px, direction changes per path ${wiggles.join(',')}`);

// ---- clicks never land dead-centre
await withPersona('https://www.reddit.com/user/alex_dev/', persona({ hesitation: 0.05 }));
const off = [];
for (let n = 0; n < 16; n++) {
  ev = [];
  const box = await page.evaluate(() => { const r = document.getElementById('chat').getBoundingClientRect(); return { l: r.left, t: r.top, w: r.width, h: r.height }; });
  await cmd({ cmd: 'clickSelector', selector: '#chat' });
  const d = ev.find((e) => e.t === 'md'); const b2 = await page.evaluate(() => { const r = document.getElementById('chat').getBoundingClientRect(); return { l: r.left, t: r.top, w: r.width, h: r.height }; });
  if (d) off.push([(d.x - (b2.l + b2.w / 2)) / b2.w, (d.yy - (b2.t + b2.h / 2)) / b2.h]);
}
const nearCentre = off.filter(([dx, dy]) => Math.abs(dx) < 0.05 && Math.abs(dy) < 0.05).length / off.length;
assert.ok(off.length >= 12 && nearCentre < 0.35 && stdev(off.map((o) => o[0])) > 0.1, `clicks spread over the button (sd ${stdev(off.map((o) => o[0])).toFixed(2)}, ${(nearCentre * 100).toFixed(0)}% near centre)`);
console.log(`✓ clicks: ${off.length} clicks on one button, horizontal spread sd ${stdev(off.map((o) => o[0])).toFixed(2)} of its width, only ${(nearCentre * 100).toFixed(0)}% near the centre`);

// ---- stray clicks only ever hit dead background
await withPersona('https://www.reddit.com/r/saas/', persona({}));
ev = []; let okc = 0;
for (let n = 0; n < 12; n++) if ((await cmd({ cmd: 'idleClick' })).ok) okc++;
const down = ev.filter((e) => e.t === 'md');
assert.ok(okc >= 8 && down.length === okc, `stray clicks happened (${okc})`);
assert.equal(down.filter((e) => e.unsafe).length, 0, 'stray clicks never land on interactive elements');
assert.equal(await page.evaluate(() => window.__log.upvotes), 0); assert.ok(page.url().endsWith('/r/saas/'));
console.log(`✓ ${okc} stray clicks on empty background: 0 on links/buttons/posts, no navigation, no votes`);

// ---- comment: slower than a DM of similar length, text exact
await withPersona('https://www.reddit.com/r/saas/comments/p1/t1/', persona({ typoRate: 0.15, typingCps: 5 }));
const C = 'Happy to help: start where buyers already complain about alternatives, then reply with something genuinely useful.';
t0 = Date.now(); r = await cmd({ cmd: 'postComment', text: C }); const cMs = Date.now() - t0;
assert.ok(r.ok, JSON.stringify(r)); assert.deepEqual(await page.evaluate(() => window.__log.comments), [C]);
console.log(`✓ comment: exact text with typos corrected, took ${(cMs / 1000).toFixed(0)}s for ${C.length} chars (includes re-read + proofread pauses)`);
await ctx.close();
console.log('\nHUMAN VARIATION PASSED');
