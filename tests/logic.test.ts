import assert from 'node:assert/strict';
import { countVariants, previewVariants, renderTemplate } from '../lib/brain';
import { effectiveCap, inActiveHours } from '../lib/governor';
import { DEFAULT_SETTINGS } from '../lib/storage';

const lead = { username: 'alex', subreddit: 'SaaS', postTitle: 'How do I find my FIRST customers?' };
const t = '{Hey|Hi|Hello} {name}, saw your post in r/{subreddit} about {topic}. {Happy to share|Glad to help} if useful?';
assert.equal(countVariants(t), 6);
assert.equal(countVariants('plain {name}'), 1);
const outs = new Set(Array.from({ length: 200 }, () => renderTemplate(t, lead)));
assert.equal(outs.size, 6, 'all 6 combos appear');
for (const o of outs) assert.ok(!/[{}|]/.test(o), 'no leftover braces: ' + o);
assert.ok([...outs][0]!.includes('alex') && [...outs][0]!.includes('r/SaaS') && [...outs][0]!.includes('how do I find my FIRST customers'));
assert.equal(previewVariants(t, lead, 4).length, 4);

// warm-up ramp
const day = 86_400_000, now = Date.now();
const s = { ...DEFAULT_SETTINGS, dailyDmCap: 40, warmup: true, warmupStart: now };
assert.equal(effectiveCap(s, now), 5);
assert.equal(effectiveCap(s, now + 3 * day), 18);
assert.equal(effectiveCap(s, now + 30 * day), 40, 'capped by dailyDmCap');
assert.equal(effectiveCap({ ...s, warmup: false }, now), 40);
// active hours incl. wrap-around
const at = (h: number) => new Date(2026, 0, 1, h);
assert.ok(inActiveHours({ ...s, activeStart: 9, activeEnd: 21 }, at(10)));
assert.ok(!inActiveHours({ ...s, activeStart: 9, activeEnd: 21 }, at(22)));
assert.ok(inActiveHours({ ...s, activeStart: 22, activeEnd: 6 }, at(2)));
assert.ok(!inActiveHours({ ...s, activeStart: 22, activeEnd: 6 }, at(12)));
console.log('logic OK');

// ---- DM gaps: right-skewed, clamped, with occasional breaks (not uniform)
import { nextGapMs } from '../lib/governor';
{
  const g = Array.from({ length: 4000 }, () => nextGapMs({ minGapSec: 60, maxGapSec: 180 }) / 1000);
  const sorted = [...g].sort((a, b) => a - b);
  const median = sorted[Math.floor(g.length / 2)]!;
  assert.ok(Math.min(...g) >= 42 - 1e-9, 'never below 0.7*min');
  assert.ok(median > 80 && median < 125, 'median near geometric mean of min/max (~104s): ' + median);
  const breaks = g.filter((x) => x > 450).length / g.length;
  assert.ok(breaks > 0.03 && breaks < 0.14, 'occasional multi-minute breaks: ' + breaks);
  assert.ok(new Set(g.map((x) => Math.round(x))).size > 200, 'many distinct gaps');
  console.log(`gaps OK  median=${median.toFixed(0)}s  p10=${sorted[400]!.toFixed(0)}s  p90=${sorted[3600]!.toFixed(0)}s  breaks=${(breaks * 100).toFixed(1)}%`);
}
