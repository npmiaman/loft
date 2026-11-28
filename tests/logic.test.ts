import assert from 'node:assert/strict';
import { countVariants, previewVariants, renderTemplate } from '../lib/brain';
import { effectiveCap, inActiveHours } from '../lib/governor';
import { DEFAULT_SETTINGS } from '../lib/storage';
import { safeHud } from '../lib/page/human';

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

// HUD text can contain a hostile post title; only our own bare <b> tags may survive as markup
assert.equal(safeHud('🕊 Reading <b>Hello</b>…'), '🕊 Reading <b>Hello</b>…');
assert.equal(safeHud('<b><img src=x onerror=alert(1)></b>'), '<b>&lt;img src=x onerror=alert(1)&gt;</b>');
assert.equal(safeHud('<script>alert("x")</script> & <b onclick="x">y</b>'), '&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; &lt;b onclick=&quot;x&quot;&gt;y</b>');
assert.ok(!/<(?!\/?b>)/i.test(safeHud('<a href=javascript:1>x</a><svg onload=1><iframe>')), 'no tag other than <b> can come through');
console.log('hud sanitizer OK');

// ================= SOP warm-up =================
import zlib from 'node:zlib';
import { accountDay, dmLimit, localDate, normalizePlan, pickSub, planStatus, remainingOf, rollTargets, startedAtForDay, subsForToday } from '../lib/sop/plan';
import { docxText, htmlText, officeText, rtfText } from '../lib/sop/office';
import { joinDocs } from '../lib/sop/compile';

const Q = (o: Partial<Record<'browse' | 'upvote' | 'comment' | 'post' | 'join' | 'dm', number>>) => ({ browse: 0, upvote: 0, comment: 0, post: 0, join: 0, dm: 0, ...o });
const rawPlan = {
  name: 'Test SOP',
  phases: [
    { name: 'Lurk', fromDay: 1, toDay: 3, goal: 'read', quota: Q({ browse: 20, upvote: 5, join: 2 }) },
    { name: 'Engage', fromDay: 5, toDay: 10, goal: 'comment', minKarma: 50, quota: Q({ browse: 30, upvote: 400, comment: 3, post: 99 }) }, // gap days 4; asks above ceilings
    { name: 'Empty', fromDay: 11, toDay: 12, quota: Q({}) }, // no activity: dropped
    { name: 'Outreach', fromDay: 9, toDay: 14, goal: 'dm', quota: Q({ upvote: 10, comment: 5, dm: 8 }) }, // overlaps Engage
  ],
  subreddits: [
    { name: 'r/SaaS', fromDay: 1, actions: ['browse', 'upvote', 'dance'], notes: 'read only at first' },
    { name: 'startups', fromDay: 5, actions: [], notes: '' },
    { name: 'bad name!', fromDay: 1, actions: [] },
    { name: 'saas', fromDay: 2, actions: [] }, // duplicate
  ],
  rules: ['no links'], manualSteps: ['verify email'], sessionsPerDay: 9,
};
{
  const p = normalizePlan(rawPlan, 1);
  assert.deepEqual(p.phases.map((x) => [x.name, x.fromDay, x.toDay]), [['Lurk', 1, 4], ['Engage', 5, 10], ['Outreach', 11, 14]], 'contiguous: gap extended, overlap goes to the earlier phase, empty phase dropped');
  assert.ok(p.warnings.some((w) => /overlap/.test(w)) && p.warnings.some((w) => /weren't covered/.test(w)), 'both repairs are reported');
  assert.equal(p.phases[1]!.quota.upvote, 80, 'upvotes capped at ceiling');
  assert.equal(p.phases[1]!.quota.post, 4, 'posts capped at ceiling');
  assert.ok(p.warnings.some((w) => /upvote/.test(w) && /400/.test(w)), 'cap is reported, not silent');
  assert.equal(p.sessionsPerDay, 6);
  assert.deepEqual(p.subreddits.map((s) => s.name), ['SaaS', 'startups'], 'r/ stripped, invalid + duplicate dropped');
  assert.deepEqual(p.subreddits[0]!.actions, ['browse', 'upvote'], 'unknown action "dance" dropped: the model cannot invent capabilities');
  assert.throws(() => normalizePlan({ phases: [{ name: 'x', fromDay: 1, toDay: 2, quota: Q({}) }] }), /No phase/);
  assert.throws(() => normalizePlan('nope'), /unexpected/);

  // account day follows the local calendar
  const t0 = new Date(2026, 5, 10, 23, 30).getTime();
  assert.equal(accountDay(t0, t0), 1);
  assert.equal(accountDay(t0, new Date(2026, 5, 11, 0, 5).getTime()), 2, 'day ticks over at local midnight, not 24h later');
  assert.equal(accountDay(startedAtForDay(6, t0), t0), 6);
  assert.equal(localDate(t0), '2026-06-10');

  // phase selection, completion, karma gate
  const now = Date.now();
  const at = (day: number) => startedAtForDay(day, now);
  let st = planStatus(p, at(2), null, now);
  assert.ok(st.state === 'active' && st.phase.name === 'Lurk' && st.day === 2);
  st = planStatus(p, at(6), 500, now);
  assert.ok(st.state === 'active' && st.phase.name === 'Engage' && !st.held);
  st = planStatus(p, at(6), 10, now);
  assert.ok(st.state === 'active' && st.phase.name === 'Lurk' && /50 karma/.test(st.held ?? ''), 'low karma holds the account in the earlier phase');
  st = planStatus(p, at(6), null, now);
  assert.ok(st.state === 'active' && st.phase.name === 'Engage', 'unreadable karma: gate ignored, not guessed');
  assert.equal(planStatus(p, at(15), 0, now).state, 'complete');

  // targets are 60-100% of the ceiling, never above, never 0 where the ceiling is >0
  for (let i = 0; i < 300; i++) {
    const t = rollTargets(p.phases[1]!);
    assert.ok(t.upvote >= 48 && t.upvote <= 80 && t.comment >= 2 && t.comment <= 3 && t.post >= 2 && t.post <= 4 && t.dm === 0 && t.join === 0, JSON.stringify(t));
  }
  assert.equal(rollTargets(p.phases[0]!, () => 0).browse, 12);
  assert.deepEqual(remainingOf(Q({ upvote: 5, comment: 2 }), Q({ upvote: 7, comment: 1 })), Q({ comment: 1 }), 'remaining never negative');

  // where to act: the SOP's own subs once their day arrives; ICP subs only if the SOP names none
  const left = Q({ browse: 10, upvote: 3, comment: 2 });
  assert.deepEqual(subsForToday(p, 1, left, ['icp1']).map((s) => [s.name, s.actions]), [['SaaS', ['browse', 'upvote']]], 'sub restricted to read+upvote; startups not live yet');
  assert.deepEqual(subsForToday(p, 6, left, []).map((s) => [s.name, s.actions.join()]), [['SaaS', 'browse,upvote'], ['startups', 'browse,upvote,comment']], 'empty actions = everything still allowed today');
  assert.deepEqual(subsForToday({ ...p, subreddits: [] }, 1, Q({ upvote: 1 }), ['icp1']).map((s) => s.name), ['icp1']);
  assert.deepEqual(subsForToday(p, 6, Q({ comment: 1 }), []).map((s) => s.name), ['startups'], 'a sub with nothing allowed left is not offered');
  const visited = new Map([['saas', 100], ['startups', 5]]);
  assert.equal(pickSub([{ name: 'SaaS' }, { name: 'startups' }], visited, () => 0)?.name, 'startups', 'least recently visited first');
  assert.equal(pickSub([], visited), null);

  // DM allowance comes from the plan, replacing the built-in ramp
  const w = { enabled: true, startedAt: at(12), plan: p };
  assert.deepEqual(dmLimit(w, null, now), { limit: 8, phase: 'Outreach', day: 12 });
  assert.equal(dmLimit({ ...w, startedAt: at(2) }, null, now)?.limit, 0, 'no DMs in the lurk phase');
  assert.equal(dmLimit({ ...w, enabled: false }, null, now), null, 'disabled: the old ramp applies');
  assert.equal(dmLimit({ ...w, startedAt: at(30) }, null, now), null, 'finished plan: no longer governs DMs');
  assert.equal(dmLimit({ ...w, plan: null }, null, now), null);
}

// documents are shortened evenly and flagged, never silently
{
  const docs = [{ id: 'a', name: 'a.txt', via: 'text', chars: 100000, text: 'x'.repeat(100000), addedAt: 0 }, { id: 'b', name: 'b.txt', via: 'text', chars: 10, text: 'short', addedAt: 0 }];
  const j = joinDocs(docs, 20000);
  assert.ok(j.truncated && j.text.includes('b.txt') && j.text.includes('short') && j.text.length < 21000);
  assert.equal(joinDocs([docs[1]!]).truncated, false);
}

// ---- Office / text formats: build real zip bytes by hand and read them back
function makeZip(files: Record<string, string>, deflate = true): Uint8Array {
  const parts: Buffer[] = [], central: Buffer[] = [];
  let offset = 0;
  for (const [name, content] of Object.entries(files)) {
    const nameB = Buffer.from(name), raw = Buffer.from(content);
    const data = deflate ? zlib.deflateRawSync(raw) : raw, method = deflate ? 8 : 0;
    const lh = Buffer.alloc(30); lh.writeUInt32LE(0x04034b50, 0); lh.writeUInt16LE(20, 4); lh.writeUInt16LE(method, 8);
    lh.writeUInt32LE(data.length, 18); lh.writeUInt32LE(raw.length, 22); lh.writeUInt16LE(nameB.length, 26);
    const ch = Buffer.alloc(46); ch.writeUInt32LE(0x02014b50, 0); ch.writeUInt16LE(20, 4); ch.writeUInt16LE(20, 6); ch.writeUInt16LE(method, 10);
    ch.writeUInt32LE(data.length, 20); ch.writeUInt32LE(raw.length, 24); ch.writeUInt16LE(nameB.length, 28); ch.writeUInt32LE(offset, 42);
    parts.push(lh, nameB, data); central.push(ch, nameB);
    offset += 30 + nameB.length + data.length;
  }
  const cd = Buffer.concat(central), eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0); eocd.writeUInt16LE(Object.keys(files).length, 8); eocd.writeUInt16LE(Object.keys(files).length, 10);
  eocd.writeUInt32LE(cd.length, 12); eocd.writeUInt32LE(offset, 16);
  return new Uint8Array(Buffer.concat([...parts, cd, eocd]));
}
{
  const body = '<w:document><w:body><w:p><w:r><w:t>Day 1 &amp; 2: lurk</w:t></w:r></w:p>' +
    '<w:p><w:r><w:t xml:space="preserve">Upvote </w:t></w:r><w:r><w:t>5 posts</w:t></w:r><w:r><w:tab/><w:t>max</w:t></w:r></w:p>' +
    '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Day</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Comments</w:t></w:r></w:p></w:tc></w:tr>' +
    '<w:tr><w:tc><w:p><w:r><w:t>3</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>2</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>';
  for (const deflate of [true, false]) {
    const t = await officeText(makeZip({ 'word/document.xml': body, '[Content_Types].xml': '<x/>' }, deflate), 'docx');
    assert.equal(t, 'Day 1 & 2: lurk\nUpvote 5 posts\tmax\nDay | Comments\n3 | 2', `docx (deflate=${deflate}): ${JSON.stringify(t)}`);
  }
  assert.ok(docxText(body).includes('Day | Comments'));

  const sheet = '<worksheet><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="D1" t="inlineStr"><is><t>note</t></is></c></row>' +
    '<row r="2"><c r="A2"><v>1</v></c><c r="B2"><v>5</v></c></row></sheetData></worksheet>';
  const x = await officeText(makeZip({
    'xl/workbook.xml': '<workbook><sheets><sheet name="Plan" sheetId="1"/></sheets></workbook>',
    'xl/sharedStrings.xml': '<sst><si><t>Day</t></si><si><t>Upvotes</t></si></sst>',
    'xl/worksheets/sheet1.xml': sheet,
  }), 'xlsx');
  assert.equal(x, '--- sheet: Plan ---\nDay | Upvotes |  | note\n1 | 5', JSON.stringify(x));

  const pp = await officeText(makeZip({
    'ppt/slides/slide2.xml': '<p:sld><a:p><a:r><a:t>Second</a:t></a:r></a:p></p:sld>',
    'ppt/slides/slide10.xml': '<p:sld><a:p><a:r><a:t>Tenth</a:t></a:r></a:p></p:sld>',
    'ppt/slides/slide1.xml': '<p:sld><a:p><a:r><a:t>First</a:t></a:r></a:p></p:sld>',
  }), 'pptx');
  assert.equal(pp, '--- slide 1 ---\nFirst\n--- slide 2 ---\nSecond\n--- slide 3 ---\nTenth', 'slides in numeric order (slide10 after slide2)');

  await assert.rejects(officeText(new Uint8Array([1, 2, 3, 4]), 'docx'), /valid zip/);
  await assert.rejects(officeText(makeZip({ 'other.xml': '<x/>' }), 'docx'), /no document body/);

  assert.equal(rtfText('{\\rtf1\\ansi{\\fonttbl{\\f0 Arial;}}\\pard Day one\\par Upvote \\b 5\\b0  posts\\par {\\*\\generator X;}\\\'e9}'), 'Day one\nUpvote 5 posts\né');
  assert.equal(htmlText('<style>x{}</style><h1>Plan</h1><p>Upvote&nbsp;5 &amp; join</p><table><tr><td>a</td><td>b</td></tr></table><script>evil()</script>'), 'Plan\nUpvote 5 & join\na | b |');
}
console.log('sop OK');

// ================= Phase 0: safety =================
import { contactedAfterClear, dmBlock, dmCap, formatUntil, isContacted, migrateSettings, startSummary, unsentLeadKeys, WRITTEN_STATUS } from '../lib/safety';
{
  const now = new Date(2026, 10, 28, 9, 0).getTime();
  const plan = normalizePlan(rawPlan, 1);
  const S = { ...DEFAULT_SETTINGS, dailyDmCap: 40, warmup: true, warmupStart: now };
  // P0-10: one cap function, plan-aware, with a reason the UI can show
  const lurk = dmCap(S, { enabled: true, startedAt: startedAtForDay(2, now), plan }, null, now);
  assert.equal(lurk.cap, 0); assert.equal(lurk.source, 'plan'); assert.match(lurk.reason, /warm-up plan phase "Lurk"/);
  const out = dmCap(S, { enabled: true, startedAt: startedAtForDay(12, now), plan }, null, now);
  assert.equal(out.cap, 8, 'plan allowance replaces the ramp'); assert.match(out.reason, /Outreach/);
  assert.equal(dmCap({ ...S, dailyDmCap: 3 }, { enabled: true, startedAt: startedAtForDay(12, now), plan }, null, now).cap, 3, 'never above the daily limit');
  const ramp = dmCap(S, { enabled: false, startedAt: 0, plan }, null, now);
  assert.equal(ramp.cap, 5); assert.equal(ramp.source, 'ramp'); assert.match(ramp.reason, /DM ramp, day 1/);
  const flat = dmCap({ ...S, warmup: false }, null, null, now);
  assert.equal(flat.cap, 40); assert.equal(flat.source, 'limit');
  assert.equal(dmCap(S, { enabled: true, startedAt: startedAtForDay(30, now), plan }, null, now).source, 'ramp', 'finished plan: ramp applies again');

  // date-aware pause times
  assert.equal(formatUntil(new Date(2026, 10, 28, 15, 5).getTime(), now), '15:05');
  assert.equal(formatUntil(new Date(2026, 10, 29, 10, 42).getTime(), now), 'tomorrow 10:42');
  assert.match(formatUntil(new Date(2026, 11, 2, 8, 0).getTime(), now), /^[A-Z][a-z]{2} 2 Dec 08:00$/);

  // status banner: pause wins, carries its reason and a resume action; plan block can't be "resumed"
  const daily = { date: '2026-11-28', count: 2, sessions: 0, nextDmAt: 0, pausedUntil: new Date(2026, 10, 29, 10, 42).getTime(), pauseReason: 'Captcha shown' };
  const b = dmBlock({ ...S, activeStart: 0, activeEnd: 24 }, daily, flat, now)!;
  assert.equal(b.kind, 'paused'); assert.equal(b.canResume, true); assert.match(b.text, /Captcha shown/); assert.match(b.text, /until tomorrow 10:42/);
  const pb = dmBlock({ ...S, activeStart: 0, activeEnd: 24 }, { ...daily, pausedUntil: 0 }, lurk, now)!;
  assert.equal(pb.kind, 'plan'); assert.equal(pb.canResume, false); assert.match(pb.text, /Lurk/);
  assert.equal(dmBlock({ ...S, activeStart: 0, activeEnd: 24 }, { ...daily, pausedUntil: 0 }, flat, now), null);
  assert.equal(dmBlock({ ...S, activeStart: 0, activeEnd: 24 }, { ...daily, pausedUntil: 0, count: 40 }, flat, now)?.kind, 'cap');

  // P0-4: whoever wrote it, a new message waits for approval; the old AI-only setting is dropped safely
  assert.equal(WRITTEN_STATUS, 'draft');
  const m = migrateSettings({ ...DEFAULT_SETTINGS, approveAiDrafts: false, dailyDmCap: 12 } as never);
  assert.ok(!('approveAiDrafts' in m)); assert.equal(m.dailyDmCap, 12, 'other settings kept');
  assert.equal(migrateSettings(undefined as never).dailyDmCap, DEFAULT_SETTINGS.dailyDmCap);
  assert.equal(startSummary('full', { cap: 5, sent: 2, ready: 7, block: null }), 'Will send up to 3 approved messages today (limit 5, 2 sent). New messages wait for your approval.');
  assert.equal(startSummary('full', { cap: 5, sent: 0, ready: 1, block: null }), 'Will send up to 1 approved message today (limit 5, 0 sent). New messages wait for your approval.');
  assert.equal(startSummary('find', { cap: 5, sent: 0, ready: 3, block: null }), 'Will not send anything. New leads go to Review.');
  assert.match(startSummary('dm', { cap: 0, sent: 0, ready: 3, block: pb }), /^Will not send any messages: /);

  // P0-6: the contacted set survives "clear", and is consulted on every dedupe check
  const L = (username: string, status: string) => ({ username, status }) as never;
  const leads = [L('Amy', 'sent'), L('bob', 'replied'), L('cat', 'draft'), L('dan', 'queued')];
  const keep = contactedAfterClear(['old_one'], leads);
  assert.deepEqual([...keep].sort(), ['amy', 'bob', 'old_one']);
  assert.ok(isContacted(keep, 'AMY') && !isContacted(keep, 'cat'));
  assert.deepEqual(unsentLeadKeys(leads).sort(), ['lead:cat', 'lead:dan'], '"Clear unsent leads" keeps sent and replied');
  console.log('phase 0 safety OK');
}


// ================= Phase 3: panel UX logic =================
import fs from 'node:fs';
import { keyCheckMessage, parseNum, setupSteps, validateSettings, viewLeads } from '../lib/panel';
{
  // P3-5 leads: search, sort, "Showing N of M", friendly statuses
  const mk = (username: string, fit: number, createdAt: number, status = 'draft', extra = {}) => ({ username, fit, createdAt, status, subreddit: 'saas', reason: '', postTitle: '', message: '', ...extra }) as never;
  const ls = [mk('amy', 70, 3), mk('bob', 90, 1), mk('cat', 80, 2, 'skipped'), mk('dan', 60, 4, 'draft', { reason: 'asked about Mailchimp' })];
  assert.deepEqual(viewLeads(ls, { status: 'all', q: '', sort: 'fit', limit: 100 }).shown.map((l: { username: string }) => l.username), ['bob', 'cat', 'amy', 'dan']);
  assert.deepEqual(viewLeads(ls, { status: 'all', q: '', sort: 'newest', limit: 100 }).shown.map((l: { username: string }) => l.username), ['dan', 'amy', 'cat', 'bob']);
  assert.deepEqual(viewLeads(ls, { status: 'all', q: 'mailchimp', sort: 'fit', limit: 100 }).shown.map((l: { username: string }) => l.username), ['dan'], 'search covers reason/message text');
  assert.deepEqual(viewLeads(ls, { status: 'skipped', q: '', sort: 'fit', limit: 100 }).shown.map((l: { username: string }) => l.username), ['cat']);
  const v = viewLeads(ls, { status: 'all', q: '', sort: 'fit', limit: 2 });
  assert.equal(v.shown.length, 2); assert.equal(v.matching, 4); assert.equal(v.label, 'Showing 2 of 4');

  // P3-5 settings: typing never writes 0, min<=max, hours 0-23
  assert.equal(parseNum('', { min: 1 }), null, 'empty while typing is not 0');
  assert.equal(parseNum('12', { min: 1 }), 12);
  assert.equal(parseNum('0', { min: 1 }), null);
  assert.equal(parseNum('25', { min: 0, max: 23 }), null);
  assert.equal(parseNum('1.5', { min: 0, int: true }), null);
  const base = { ...DEFAULT_SETTINGS };
  assert.deepEqual(validateSettings(base), {});
  assert.match(validateSettings({ ...base, minGapSec: 300, maxGapSec: 100 }).maxGapSec ?? '', /at least the min gap/);
  assert.match(validateSettings({ ...base, activeStart: 9, activeEnd: 9 }).activeEnd ?? '', /differ/);
  assert.match(validateSettings({ ...base, activeStart: 30 }).activeStart ?? '', /0 and 23/);
  assert.equal(keyCheckMessage({ ok: true }).kind, 'success');
  assert.match(keyCheckMessage({ ok: false, status: 401 }).text, /rejected this key/);
  assert.match(keyCheckMessage({ ok: false, status: 402 }).text, /balance/);

  // P3-2 first run: ordered steps, the first missing one is what Start points at
  const steps = setupSteps({ ...base, deepseekKey: '' }, null, [], false);
  assert.deepEqual(steps.map((s) => s.id), ['key', 'product', 'subreddits', 'safety']);
  assert.equal(steps.find((s) => !s.done)?.id, 'key');
  const s2 = setupSteps({ ...base, deepseekKey: 'sk', myUsername: 'me' }, { name: 'x' } as never, [{ enabled: true }] as never, true);
  assert.ok(s2.every((s) => s.done));
  assert.equal(setupSteps({ ...base, deepseekKey: 'sk' }, { name: 'x' } as never, [{ enabled: true }] as never, false).find((s) => !s.done)?.id, 'safety', 'safety needs the Reddit username');

  // P3-4 design lint: tokens, type scale, focus, hit targets, no inline spacing, no emoji in buttons
  const css = fs.readFileSync('entrypoints/sidepanel/style.css', 'utf8');
  const tokenBlocks = css.match(/:root[^{]*\{[^}]*\}/g) ?? [];
  const outside = tokenBlocks.reduce((c, b) => c.replace(b, ''), css.replace(/@media\s*\(prefers-color-scheme:\s*dark\)\s*\{/, ''));
  assert.deepEqual(outside.match(/#[0-9a-fA-F]{3,8}\b/g) ?? [], [], 'raw hex colours only inside :root token blocks');
  assert.ok(/prefers-color-scheme:\s*dark/.test(css), 'dark mode');
  for (const m of css.matchAll(/font(?:-size)?:\s*(?:\d+\s+)?([\d.]+)px/g)) assert.ok(Number(m[1]) >= 11, 'no text under 11px: ' + m[0]);
  assert.ok(/:focus-visible/.test(css), 'visible focus style');
  assert.match(css, /\.btn\.sm\{[^}]*min-height:28px/, 'small buttons are at least 28px tall');
  assert.ok(!/\.drop input\{display:none\}/.test(css), 'file input is visually hidden, not display:none');
  const tsx = fs.readdirSync('entrypoints/sidepanel/tabs').map((f) => fs.readFileSync('entrypoints/sidepanel/tabs/' + f, 'utf8')).concat(fs.readFileSync('entrypoints/sidepanel/App.tsx', 'utf8'), fs.readFileSync('entrypoints/sidepanel/ui.tsx', 'utf8')).join('\n');
  assert.deepEqual(tsx.match(/style=\{\{[^}]*margin[^}]*\}\}/g) ?? [], [], 'spacing uses utility classes, not inline margins');
  assert.deepEqual(tsx.match(/<button[^>]*>[^<]*[▶■✨✕↗⏳🕊][^<]*<\/button>/gu) ?? [], [], 'no emoji/glyphs in buttons');
  const cur = fs.readFileSync('lib/page/cursor.ts', 'utf8');
  const hudRule = cur.match(/\.hud\{[^}]*\}/)?.[0] ?? '';
  assert.ok(/top:/.test(hudRule) && /left:50%/.test(hudRule) && !/bottom:/.test(hudRule), 'HUD sits top-centre, away from Reddit chat: ' + hudRule);
  assert.ok(/aria-label="Stop Pigeon"/.test(cur) && /focus-visible/.test(cur) && /Escape/.test(cur), 'HUD Stop is labelled, focusable, Esc works');
  const curCss = cur.match(/const CSS = `([\s\S]*?)`;/)?.[1] ?? '';
  assert.deepEqual(curCss.replace(/:host\{[^}]*\}/, '').match(/#[0-9a-fA-F]{3,8}\b/g) ?? [], [], 'cursor colours come from its :host tokens');
  console.log('phase 3 panel OK');
}
