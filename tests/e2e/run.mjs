import { chromium } from 'playwright';
import { pages } from './mock.mjs';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const EXT = new URL('../../.output/chrome-mv3', import.meta.url).pathname;
const dir = fs.mkdtempSync('/tmp/pw-ext-');
const ctx = await chromium.launchPersistentContext(dir, {
  channel: 'chromium', headless: true,
  args: [`--disable-extensions-except=${EXT}`, `--load-extension=${EXT}`],
});
let [sw] = ctx.serviceWorkers();
if (!sw) sw = await ctx.waitForEvent('serviceworker');

await ctx.route('https://www.reddit.com/**', (route) => {
  const u = new URL(route.request().url());
  let html;
  if (u.pathname.startsWith('/user/')) html = pages.profile(u.pathname.split('/')[2]);
  else if (u.pathname.includes('/comments/')) html = pages.postPage();
  else html = pages.listing();
  route.fulfill({ contentType: 'text/html', body: html });
});

const page = await ctx.newPage();
page.on('pageerror', (e) => console.log('PAGE ERROR', e.message));
const tabIdOf = async () => sw.evaluate(async () => (await chrome.tabs.query({ url: 'https://www.reddit.com/*' }))[0].id);
const cmd = async (c) => { const id = await tabIdOf(); return sw.evaluate(([id, c]) => chrome.tabs.sendMessage(id, { target: 'page', ...c }), [id, c]); };
const waitReady = async () => { for (let i = 0; i < 40; i++) { try { const r = await cmd({ cmd: 'ping' }); if (r?.ok) return; } catch {} await new Promise((r) => setTimeout(r, 250)); } throw new Error('content script never ready'); };
const t0 = Date.now(); const lap = (m) => console.log(`✓ ${m} (${((Date.now() - t0) / 1000).toFixed(1)}s)`);

await page.goto('https://www.reddit.com/r/saas/'); await waitReady(); lap('content script injected');

// 1. browse
const b = await cmd({ cmd: 'browse', maxPosts: 10, seconds: 10 });
assert.ok(b.ok, JSON.stringify(b));
const authors = b.posts.map((p) => p.author).sort();
assert.deepEqual(authors, ['dev_bob', 'founder_amy', 'sam_k'], 'reads posts, drops AutoModerator: ' + authors);
assert.ok(b.posts[0].body.length > 20 && b.posts.find((p) => p.author === 'founder_amy').score === 10);
lap(`browse read ${b.posts.length} posts`);
assert.ok(await page.evaluate(() => window.scrollY) > 100, 'page was scrolled');
lap('scrolled like a user');
assert.equal(await page.evaluate(() => !!document.querySelector('pigeon-cursor')), true);

// 2. clickLink -> navigation
const cl = await cmd({ cmd: 'clickLink', hrefIncludes: '/r/saas/comments/p1' });
assert.ok(cl.ok);
await page.waitForURL('**/comments/p1/**', { timeout: 15000 }); await waitReady();
lap('cursor clicked a link and navigated');

// 3. readPost
const rp = await cmd({ cmd: 'readPost' });
assert.ok(rp.ok && rp.post.author === 'founder_amy', JSON.stringify(rp).slice(0, 200));
assert.deepEqual(rp.comments.map((c) => c.author).sort(), ['reader_one', 'reader_two']);
lap(`readPost: post + ${rp.comments.length} comments (bot filtered)`);

// 4. upvote (button lives in a shadow root)
await page.evaluate(() => window.scrollTo(0, 0));
const up = await cmd({ cmd: 'upvote', scope: 'post', permalink: '/r/saas/comments/p1/t1/' });
assert.ok(up.ok && !up.already, JSON.stringify(up));
const up2 = await cmd({ cmd: 'upvote', scope: 'post', permalink: '/r/saas/comments/p1/t1/' });
assert.ok(up2.already, 'second upvote is a no-op');
const uc = await cmd({ cmd: 'upvote', scope: 'comment', permalink: '/r/saas/comments/p1/t1/c1/' });
assert.ok(uc.ok, JSON.stringify(uc));
assert.equal(await page.evaluate(() => window.__log.upvotes), 2);
lap('upvoted post + comment through shadow DOM; never double-votes');

// 5. postComment
const pc = await cmd({ cmd: 'postComment', text: 'Happy to help: I would start where buyers already complain.' });
assert.ok(pc.ok, JSON.stringify(pc));
assert.deepEqual(await page.evaluate(() => window.__log.comments), ['Happy to help: I would start where buyers already complain.']);
lap('typed + posted a comment');

// 6. DM
await page.goto('https://www.reddit.com/user/alex_dev/'); await waitReady();
const msg = "Hey alex_dev, saw your post about finding first customers. I build a tool for that, curious how you're doing it today?";
const d = await cmd({ cmd: 'dm', text: msg });
assert.ok(d.ok, JSON.stringify(d));
assert.deepEqual(await page.evaluate(() => window.__log.dms), [msg]);
lap('opened chat, typed human-paced, sent DM, verified composer cleared');

// 7. rate-limit is detected, not reported as success
await page.goto('https://www.reddit.com/user/ratelimited/'); await waitReady();
const rl = await cmd({ cmd: 'dm', text: 'hello there friend, quick question for you' });
assert.equal(rl.ok, false); assert.equal(rl.code, 'rate-limit', JSON.stringify(rl));
lap('rate-limit banner detected → code rate-limit');

// 8. abort interrupts a long command
await page.goto('https://www.reddit.com/r/saas/').catch(() => {});
await waitReady();
const long2 = cmd({ cmd: 'browse', maxPosts: 50, seconds: 60 });
await new Promise((r) => setTimeout(r, 1500));
await cmd({ cmd: 'abort' });
const res = await Promise.race([long2, new Promise((r) => setTimeout(() => r('TIMEOUT'), 5000))]);
assert.ok(res !== 'TIMEOUT' && res.aborted, 'abort stops a running browse: ' + JSON.stringify(res));
lap('abort stops a 60s browse within seconds');

await page.screenshot({ path: '/tmp/pigeon-mock.png' });
await ctx.close();
console.log('\nALL E2E MECHANICS PASSED');
