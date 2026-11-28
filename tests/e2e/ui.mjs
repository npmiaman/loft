import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import fs from 'node:fs';
const EXT = new URL('../../.output/chrome-mv3', import.meta.url).pathname;
const ctx = await chromium.launchPersistentContext(fs.mkdtempSync('/tmp/pw-ext-'), { channel: 'chromium', headless: true, viewport: { width: 420, height: 900 }, args: [`--disable-extensions-except=${EXT}`, `--load-extension=${EXT}`] });
let [sw] = ctx.serviceWorkers(); if (!sw) sw = await ctx.waitForEvent('serviceworker');
const id = new URL(sw.url()).host;

const seen = { spin: 0, bodies: [] };
await ctx.route('https://api.deepseek.com/**', async (route) => {
  const body = JSON.parse(route.request().postData());
  seen.bodies.push(body);
  const sys = body.messages[0].content;
  let out;
  if (sys.includes('spintax writer')) {
    seen.spin++;
    out = seen.spin === 1
      ? { template: '{Hey|Hi|Hello} there, saw your post in r/{subreddit} about {topic}. {Happy to share|Glad to help} if useful?' } // drops {name} -> must be rejected
      : { template: '{Hey|Hi|Hello} {name}, {saw|spotted|came across} your post in r/{subreddit} about {topic}. {Happy to share|Glad to help|Keen to help} if {useful|it helps}?' };
  } else if (sys.includes('judge whether a subreddit')) {
    const names = [...body.messages[1].content.matchAll(/### r\/(\w+)/g)].map((m) => m[1]);
    out = { subs: names.map((n) => ({ name: n, icpDensity: n.toLowerCase() === 'saas' ? 80 : 40, rulesRisk: n.toLowerCase() === 'entrepreneur' ? 'high' : 'low', reason: 'mock reason for ' + n })) };
  } else out = {};
  await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ choices: [{ message: { content: JSON.stringify(out) } }] }) });
});
await ctx.route('https://www.reddit.com/**', (route) => {
  const u = new URL(route.request().url());
  const name = u.pathname.split('/')[2];
  const now = Math.floor(Date.now() / 1000);
  if (u.pathname.endsWith('about.json')) return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ data: { display_name: name, subscribers: 120000, public_description: 'desc', over18: false, subreddit_type: 'public' } }) });
  if (u.pathname.endsWith('rules.json')) return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ rules: [{ short_name: 'No spam', description: 'no promotion' }] }) });
  if (u.pathname.endsWith('new.json')) return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ data: { children: Array.from({ length: 25 }, (_, i) => ({ data: { title: 'post ' + i, created_utc: now - i * 1800 } })) } }) });
  route.fulfill({ body: '' });
});

await sw.evaluate(() => chrome.storage.local.set({
  settings: { deepseekKey: 'sk-test', model: 'deepseek-flash', dailyDmCap: 40, warmup: true, warmupStart: Date.now(), activeStart: 0, activeEnd: 23, minGapSec: 60, maxGapSec: 180, minFit: 70, sessionMinutes: 30, upvotes: true, maxUpvotesPerSession: 10, dmMode: 'template', approveAiDrafts: true, aiGuidance: '', autoSchedule: false, sessionsPerDay: 3, myUsername: '' },
  product: { url: 'https://x.com', name: 'Pigeon', summary: 's', offer: 'o', icp: 'founders', personas: [], painPhrases: [], buyingSignals: [], competitors: [], subredditIdeas: ['SaaS', 'Entrepreneur', 'startups'], searchQueries: [] },
}));

const page = await ctx.newPage();
page.on('pageerror', (e) => console.log('PAGE ERROR', e.message));
await page.goto(`chrome-extension://${id}/sidepanel.html`);
await page.screenshot({ path: '/tmp/pigeon-run.png' });
assert.ok(await page.getByText('Run the agent').isVisible()); console.log('✓ panel renders (Run tab)');

// ---- Messages: spintax writer with validation + retry
// P3-1: the template now lives in Setup (was its own Messages tab)
await page.locator('nav.tabs').getByRole('button', { name: 'Setup' }).click();
const tpl = page.locator('textarea.tall');
await tpl.fill('Hey {name}, saw your post in r/{subreddit} about {topic}. Happy to share if useful?');
assert.ok(await page.getByText('No spintax yet').isVisible()); console.log('✓ warns that a plain template has no variation');
await page.getByRole('button', { name: /Add spintax with AI/ }).click();
await page.getByText(/can render/).waitFor({ timeout: 10000 });
assert.equal(seen.spin, 2, 'first (invalid) answer rejected, second accepted');
assert.ok(seen.bodies.find((b) => b.messages[0].content.includes('previous attempt was rejected: lost variable {name}')), 'retry prompt carried the validator feedback');
const text = await tpl.inputValue();
assert.ok(text.includes('{name}') && /\{Hey\|Hi\|Hello\}/.test(text)); 
console.log('✓ spintax writer: rejected output that dropped {name}, retried with feedback, accepted valid one');
console.log('   variants:', (await page.getByText(/can render/).innerText()));
assert.equal(seen.bodies.at(-1).thinking.type, 'disabled'); assert.equal(seen.bodies.at(-1).response_format.type, 'json_object'); assert.equal(seen.bodies.at(-1).model, 'deepseek-flash');
console.log('✓ request shape: model=deepseek-flash, json_object, thinking disabled');
await page.getByRole('button', { name: 'Preview variations' }).click();
const previews = await page.locator('.preview').allInnerTexts();
assert.ok(previews.every((p) => !/\?\./.test(p)), 'no stray ?. from topic'); assert.ok(previews.length >= 3 && new Set(previews).size === previews.length && previews.every((p) => !/[{}|]/.test(p) && p.includes('alex_dev')), previews.join('\n'));
console.log('✓ previews render distinct, fully-filled messages:\n   - ' + previews.slice(0, 3).join('\n   - '));
await page.getByRole('button', { name: 'Undo AI spin' }).click();
assert.ok((await tpl.inputValue()).startsWith('Hey {name}')); console.log('✓ undo restores the original');
await page.getByRole('button', { name: /Add spintax with AI/ }).click(); await page.getByText(/can render/).waitFor();
await page.screenshot({ path: '/tmp/pigeon-messages.png' });

// ---- Setup: subreddit discovery (real fetch path, mocked Reddit + DeepSeek)
await page.getByRole('button', { name: 'Find subreddits' }).click();
for (let i = 0; i < 60; i++) { const n = await sw.evaluate(async () => ((await chrome.storage.local.get('subreddits')).subreddits ?? []).length); if (n) break; await new Promise((r) => setTimeout(r, 500)); }
console.log('page errors/status:', JSON.stringify(await page.locator('.notice').allInnerTexts()));
const subs = await sw.evaluate(async () => (await chrome.storage.local.get('subreddits')).subreddits);
console.log('✓ discovery:', subs.map((s) => `${s.name} score=${s.score} risk=${s.rulesRisk} on=${s.enabled}`).join(' | '));
assert.equal(subs.find((s) => s.name === 'entrepreneur').enabled, false, 'high-risk rules => disabled by default');
assert.equal(subs.find((s) => s.name === 'saas').enabled, true);
assert.equal(subs[0].name, 'saas', 'sorted by score');
await page.screenshot({ path: '/tmp/pigeon-setup.png' });
await ctx.close();
console.log('\nUI + BRAIN PLUMBING PASSED');
