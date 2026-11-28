// The agent loop. Runs in the service worker and drives one dedicated Reddit window through the content script.
import * as store from './storage';
import { getContacted, getLead, getSettings, isKnown, listLeads, markContacted, patchLead, putLead } from './storage';
import { WRITTEN_STATUS } from './safety';
import { draftComment, renderTemplate, scoreItems, writeDm, type ScoreItem, type ScoreResult } from './brain';
import { checkDm, inActiveHours, pauseFor, recordDm } from './governor';
import { planStatus } from './sop/plan';
import { commitItem, getWarmup, runWarmup, type Io } from './sop/run';
import type { AgentState, CommentData, DmResult, Lead, PostData, Product, RunMode, Settings, Subreddit } from './types';

class Stop extends Error {}
let stopFlag = false;
let running = false;
let tabId: number | null = null;
let keepalive: ReturnType<typeof setInterval> | null = null;
let state: AgentState = { ...(null as unknown as AgentState) };

export const isRunning = () => running;
import { clamp, lognormal, rand } from './rand';
/** Right-skewed pause around `ms`. */
const jit = (ms: number, sigma = 0.45) => lognormal(ms, sigma);
const check = () => {
  if (stopFlag) throw new Stop();
};
async function wait(ms: number) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    check();
    await new Promise((r) => setTimeout(r, Math.min(500, Math.max(0, end - Date.now()))));
  }
  check();
}

function upd(patch: Partial<AgentState>) {
  state = { ...state, ...patch };
  return store.set('agent', state);
}
function bump(k: keyof AgentState['stats'], n = 1) {
  state = { ...state, stats: { ...state.stats, [k]: state.stats[k] + n } };
  return store.set('agent', state);
}

// ---------------------------------------------------------------- tab + messaging
async function waitContent(id: number, timeout = 25_000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    check();
    try {
      const t = await chrome.tabs.get(id);
      if (t.status === 'complete') {
        const r = await chrome.tabs.sendMessage(id, { target: 'page', cmd: 'ping' });
        if (r?.ok) return;
      }
    } catch { /* not ready yet */ }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error('Reddit page did not become ready (is the Pigeon window logged in and visible?)');
}

async function ensureTab(): Promise<number> {
  if (tabId != null) {
    try {
      await chrome.tabs.get(tabId);
      return tabId;
    } catch { tabId = null; }
  }
  const w = await chrome.windows.create({ url: 'https://www.reddit.com/', type: 'normal', focused: false, width: 1280, height: 900 });
  const first = w?.tabs?.[0]?.id;
  if (first == null) throw new Error('Could not open the Pigeon Reddit window');
  tabId = first;
  await waitContent(tabId);
  return tabId;
}

async function cmd<T = any>(c: Record<string, unknown>, timeoutMs = 180_000): Promise<T> {
  let lastErr: unknown;
  for (let attempt = 0; attempt < 3; attempt++) {
    check();
    const id = await ensureTab();
    try {
      const res: any = await Promise.race([
        chrome.tabs.sendMessage(id, { target: 'page', ...c }),
        new Promise((_, rej) => setTimeout(() => rej(new Error(`Timed out: ${c.cmd}`)), timeoutMs)),
      ]);
      if (res?.aborted) throw new Stop();
      return res as T;
    } catch (e) {
      if (e instanceof Stop) throw e;
      lastErr = e;
      await new Promise((r) => setTimeout(r, 1000));
      try { await waitContent(id, 10_000); } catch { /* retry */ }
    }
  }
  throw lastErr instanceof Error ? lastErr : new Error(String(lastErr));
}

const hud = (text: string) => cmd({ cmd: 'hud', text }, 5000).catch(() => {});

function completed(id: number, timeout: number): Promise<void> {
  return new Promise((resolve) => {
    const t = setTimeout(done, timeout);
    function h(tid: number, info: { status?: string }) {
      if (tid === id && info.status === 'complete') done();
    }
    function done() {
      clearTimeout(t);
      chrome.tabs.onUpdated.removeListener(h);
      resolve();
    }
    chrome.tabs.onUpdated.addListener(h);
  });
}

async function navigate(url: string) {
  const id = await ensureTab();
  const done = completed(id, 25_000);
  await chrome.tabs.update(id, { url });
  await done;
  await waitContent(id);
  await wait(clamp(jit(1900, 0.4), 900, 6000));
}

async function clickNav(hrefIncludes: string, fallbackUrl: string): Promise<boolean> {
  const id = await ensureTab();
  const r = await cmd<{ ok: boolean }>({ cmd: 'clickLink', hrefIncludes }, 10_000);
  if (r?.ok) {
    const needle = hrefIncludes.toLowerCase();
    const end = Date.now() + 10_000;
    while (Date.now() < end) {
      check();
      const t = await chrome.tabs.get(id);
      if ((t.url ?? '').toLowerCase().includes(needle) && t.status === 'complete') {
        await waitContent(id);
        await wait(clamp(jit(1500, 0.4), 700, 5000));
        return true;
      }
      await new Promise((res) => setTimeout(res, 400));
    }
  }
  await navigate(fallbackUrl);
  return false;
}

// ---------------------------------------------------------------- brain glue
async function ingest(
  product: Product,
  s: Settings,
  entries: (ScoreItem & { postUrl: string; postTitle: string; source: Lead['source'] })[],
): Promise<Map<string, ScoreResult>> {
  const fresh: typeof entries = [];
  const seenNow = new Set<string>();
  for (const e of entries) {
    const key = e.author.toLowerCase();
    if (!e.author || key === s.myUsername.toLowerCase() || e.text.trim().length < 25) continue;
    if (seenNow.has(e.id)) continue;
    seenNow.add(e.id);
    fresh.push(e);
  }
  const out = new Map<string, ScoreResult>();
  if (!fresh.length) return out;
  const results = await scoreItems(product, fresh);
  await bump('scanned', fresh.length);
  const contacted = await getContacted();
  for (const r of results) {
    out.set(r.id, r);
    const e = fresh.find((x) => x.id === r.id);
    if (!e || !r.dm_ok || r.fit < s.minFit) continue;
    if (contacted.has(e.author.toLowerCase())) continue; // already messaged once: never queue them again
    const existing = await getLead(e.author);
    if (existing && (existing.status !== 'queued' || existing.fit >= r.fit)) continue;
    await putLead({
      username: e.author,
      subreddit: e.subreddit,
      postUrl: e.postUrl,
      postTitle: e.postTitle,
      snippet: e.text.slice(0, 600),
      fit: r.fit,
      intent: r.intent,
      reason: r.reason,
      status: 'queued',
      source: e.source,
      createdAt: Date.now(),
    });
    if (!existing) {
      await bump('leads');
      store.log('ok', `Lead u/${e.author} · fit ${r.fit} · ${r.reason}`);
    }
  }
  return out;
}

function pickSubreddit(subs: Subreddit[]): Subreddit | null {
  const on = subs.filter((x) => x.enabled);
  if (!on.length) return null;
  const pool = [...on].sort((a, b) => (a.lastVisited ?? 0) - (b.lastVisited ?? 0)).slice(0, 4);
  const total = pool.reduce((n, x) => n + x.score + 10, 0);
  let r = Math.random() * total;
  for (const x of pool) if ((r -= x.score + 10) <= 0) return x;
  return pool[0] ?? null;
}

let upvotesThisSession = 0;
async function maybeUpvote(s: Settings, scope: 'post' | 'comment', permalink: string) {
  if (!s.upvotes || upvotesThisSession >= s.maxUpvotesPerSession) return;
  const r = await cmd<{ ok: boolean; already?: boolean }>({ cmd: 'upvote', scope, permalink }, 30_000);
  if (r?.ok && !r.already) {
    upvotesThisSession++;
    await bump('upvotes');
  }
}

let oppDrafts = 0;
async function maybeDraftOpportunity(product: Product, post: PostData, r: ScoreResult) {
  if (!r.comment_fit || oppDrafts >= 5) return;
  const opps = await store.get('opportunities');
  if (opps.some((o) => o.id === post.id)) return;
  const d = await draftComment(product, post);
  if (!d.comment) return;
  oppDrafts++;
  opps.unshift({
    id: post.id,
    postUrl: 'https://www.reddit.com' + post.permalink,
    title: post.title,
    subreddit: post.subreddit,
    author: post.author,
    draft: d.comment,
    why: d.why ?? '',
    status: 'pending',
    createdAt: Date.now(),
  });
  await store.set('opportunities', opps.slice(0, 100));
  store.log('info', `Comment opportunity drafted in r/${post.subreddit}: ${post.title.slice(0, 60)}`);
}

async function visitSubreddit(product: Product, s: Settings, sub: Subreddit) {
  await upd({ phase: 'browsing', subreddit: sub.name });
  store.log('info', `Browsing r/${sub.name}`);
  await hud(`🕊 Browsing <b>r/${sub.name}</b>`);
  await clickNav(`/r/${sub.name}`, `https://www.reddit.com/r/${sub.name}/`);
  const subs = await store.get('subreddits');
  await store.set('subreddits', subs.map((x) => (x.name === sub.name ? { ...x, lastVisited: Date.now() } : x)));

  const br = await cmd<{ posts: PostData[] }>({ cmd: 'browse', maxPosts: 25, seconds: Math.round(clamp(jit(36, 0.35), 18, 85)) }, 300_000);
  const posts = br.posts ?? [];
  store.log('info', `Read ${posts.length} posts in r/${sub.name}`);
  if (!posts.length) {
    store.log('warn', `No posts could be read in r/${sub.name} — Reddit's DOM may have changed or the page didn't load.`);
    return;
  }

  await upd({ phase: 'scoring' });
  await hud(`🕊 Reading people in <b>r/${sub.name}</b>…`);
  const results = await ingest(
    product,
    s,
    posts.map((p) => ({
      id: p.id, kind: 'post' as const, author: p.author, subreddit: p.subreddit || sub.name, title: p.title,
      text: p.body || p.title, postUrl: p.permalink, postTitle: p.title, source: 'browse' as const,
    })),
  );

  const ranked = posts
    .map((p) => ({ p, r: results.get(p.id) }))
    .filter((x): x is { p: PostData; r: ScoreResult } => !!x.r && x.r.fit >= Math.max(40, s.minFit - 15))
    .sort((a, b) => b.r.fit - a.r.fit)
    .slice(0, 2);

  for (const { p, r } of ranked) {
    check();
    await upd({ phase: 'reading post' });
    await hud(`🕊 Reading <b>${p.title.slice(0, 40)}…</b>`);
    await clickNav(p.permalink, 'https://www.reddit.com' + p.permalink);
    const rp = await cmd<{ post: PostData | null; comments: CommentData[] }>({ cmd: 'readPost' }, 240_000);
    if (r.fit >= 75) await maybeUpvote(s, 'post', p.permalink);
    await maybeDraftOpportunity(product, rp.post ?? p, r);
    const cr = await ingest(
      product,
      s,
      (rp.comments ?? []).map((c) => ({
        id: c.id, kind: 'comment' as const, author: c.author, subreddit: p.subreddit || sub.name, title: p.title,
        text: c.text, postUrl: c.permalink, postTitle: p.title, source: 'browse' as const,
      })),
    );
    const best = [...cr.entries()].filter(([, v]) => v.fit >= 80 && v.dm_ok).slice(0, 2);
    for (const [id] of best) await maybeUpvote(s, 'comment', id);
  }
}

// ---------------------------------------------------------------- DMs
async function prepareQueued(product: Product, s: Settings, max: number) {
  const queued = (await listLeads()).filter((l) => l.status === 'queued').slice(0, max);
  if (!queued.length) return;
  const templates = await store.get('templates');
  for (const l of queued) {
    check();
    let message = '';
    const useAi = s.dmMode === 'ai' && !!l.snippet;
    try {
      message = useAi ? await writeDm(product, l, s) : renderTemplate((templates[0] ?? store.DEFAULT_TEMPLATE).body, l);
    } catch (e) {
      store.log('warn', `Could not write DM for u/${l.username}: ${(e as Error).message}`);
      continue;
    }
    if (!message) continue;
    // Every message waits for approval, whoever wrote it (AI, template, page scan, import).
    await patchLead(l.username, { message, status: WRITTEN_STATUS });
    store.log('info', `Draft waiting for your approval: u/${l.username}`);
  }
}

let consecutiveFails = 0;
async function sendDm(lead: Lead): Promise<void> {
  if ((await getContacted()).has(lead.username.toLowerCase())) {
    store.log('warn', `Skipped u/${lead.username}: already messaged before.`);
    await patchLead(lead.username, { status: 'skipped', error: 'Already messaged before' });
    return;
  }
  await upd({ phase: 'messaging', subreddit: '' });
  store.log('info', `DMing u/${lead.username}`);
  await hud(`🕊 Messaging <b>u/${lead.username}</b>`);
  await navigate(`https://www.reddit.com/user/${encodeURIComponent(lead.username)}/`);
  let res = await cmd<DmResult>({ cmd: 'dm', text: lead.message }, 240_000);
  if (!res.ok && res.code === 'no-chat-button') {
    store.log('info', 'No chat button; trying the message-compose page');
    await navigate(`https://www.reddit.com/message/compose/?to=${encodeURIComponent(lead.username)}`);
    res = await cmd<DmResult>({ cmd: 'dm', text: lead.message }, 240_000);
  }
  if (res.ok) {
    consecutiveFails = 0;
    await patchLead(lead.username, { status: 'sent', sentAt: Date.now(), error: undefined });
    await markContacted(lead.username);
    await recordDm();
    await bump('dms');
    store.log('ok', `Sent DM to u/${lead.username}`);
    return;
  }
  await bump('errors');
  await patchLead(lead.username, { status: res.code === 'blocked' ? 'skipped' : 'failed', error: res.error ?? res.code });
  store.log('error', `DM to u/${lead.username} failed: ${res.error ?? res.code}`);
  if (res.code === 'rate-limit') {
    await pauseFor(6 * 3_600_000, 'Reddit is rate limiting DMs');
    store.log('warn', 'Reddit is rate limiting DMs. Paused DMs for 6 hours.');
    throw new Stop();
  }
  if (res.code === 'captcha') {
    await pauseFor(24 * 3_600_000, 'Reddit showed a captcha. Solve it in the Pigeon window first');
    store.log('warn', 'Captcha shown. Paused for 24h — solve it manually in the Pigeon window, then resume.');
    throw new Stop();
  }
  if (res.code !== 'blocked' && ++consecutiveFails >= 3) {
    await pauseFor(3_600_000, 'Three DMs failed in a row. Reddit\u2019s chat may have changed');
    store.log('warn', 'DM flow failed 3 times in a row — Reddit’s chat UI may have changed. Stopping. Check the log.');
    throw new Stop();
  }
}

/** Returns true when it's pointless to keep trying DMs this session. */
async function dmTick(product: Product, s: Settings, max: number): Promise<boolean> {
  for (let i = 0; i < max; i++) {
    check();
    const g = await checkDm();
    if (!g.ok) {
      if (g.hard) store.log('info', `DMs held: ${g.reason}`);
      return !!g.hard;
    }
    let leads = (await listLeads()).filter((l) => l.status === 'ready' && l.message);
    if (!leads.length) {
      await prepareQueued(product, s, 3);
      leads = (await listLeads()).filter((l) => l.status === 'ready' && l.message);
    }
    if (!leads.length) return false;
    const next = leads[0];
    if (!next) return false;
    await sendDm(next);
  }
  return false;
}

// ---------------------------------------------------------------- SOP warm-up plumbing
async function currentUrl(): Promise<string> {
  return tabId == null ? '' : ((await chrome.tabs.get(tabId)).url ?? '');
}

function warmupIo(product: Product | null, s: Settings): Io {
  return {
    check, wait, cmd, navigate, clickNav, hud, url: currentUrl, upd, bump,
    // true once DMs are finished for this session: blocked by a gate, or nothing left to send
    dm: async () => {
      if (!product) return true;
      if (await dmTick(product, s, 1)) return true;
      return !(await listLeads()).some((l) => l.status === 'ready' || l.status === 'queued');
    },
  };
}

// ---------------------------------------------------------------- session
async function runSession(mode: RunMode) {
  const s = await getSettings();
  const product = await store.get('product');
  const subs = await store.get('subreddits');
  if (mode === 'warmup') {
    await ensureTab();
    await runWarmup(warmupIo(product, s), s.sessionMinutes);
    return;
  }
  if (mode !== 'dm') {
    if (!product) throw new Error('Set up your product first (Setup tab).');
    if (!subs.some((x) => x.enabled)) throw new Error('No subreddits enabled. Find subreddits in the Setup tab.');
  }
  if ((mode === 'dm' && s.dmMode === 'ai' && !product) || !s.deepseekKey) {
    if (mode !== 'dm' || s.dmMode === 'ai') throw new Error('Add your DeepSeek key in Settings.');
  }
  if (s.warmup && !s.warmupStart) await store.set('settings', { ...s, warmupStart: Date.now() });

  await ensureTab();
  const end = Date.now() + s.sessionMinutes * 60_000;
  const prod = product as Product;

  if (mode === 'dm') {
    while (Date.now() < end) {
      const done = await dmTick(prod, s, 1);
      if (done) break;
      const g = await checkDm();
      const anyLeft = (await listLeads()).some((l) => l.status === 'ready' || l.status === 'queued');
      if (!anyLeft) break;
      await upd({ phase: 'waiting' });
      await hud('🕊 Waiting before the next DM…');
      await wait(g.ok ? 5_000 : Math.min(g.waitMs ?? 20_000, 120_000));
    }
    return;
  }

  while (Date.now() < end) {
    check();
    const fresh = await store.get('subreddits');
    const sub = pickSubreddit(fresh);
    if (!sub) break;
    await visitSubreddit(prod, s, sub);
    if (mode === 'full') {
      const hard = await dmTick(prod, s, 2);
      if (hard && !inActiveHours(s)) break;
    }
    await upd({ phase: 'idle between subs' });
    await wait(clamp(jit(7_000, 0.55), 3_000, 40_000));
  }
}

export async function startAgent(mode: RunMode): Promise<{ ok: boolean; error?: string }> {
  if (running) return { ok: false, error: 'Agent is already running.' };
  running = true;
  stopFlag = false;
  upvotesThisSession = 0;
  oppDrafts = 0;
  consecutiveFails = 0;
  state = { ...store.EMPTY_AGENT, running: true, mode, phase: 'starting', startedAt: Date.now() };
  await store.set('agent', state);
  keepalive = setInterval(() => chrome.runtime.getPlatformInfo().catch(() => {}), 10_000);
  store.log('info', `Session started (${mode})`);

  (async () => {
    try {
      await runSession(mode);
      store.log('ok', 'Session finished');
    } catch (e) {
      if (e instanceof Stop) store.log('info', 'Stopped');
      else {
        const m = (e as Error).message ?? String(e);
        store.log('error', m);
        await bump('errors');
        await upd({ lastError: m });
      }
    } finally {
      if (keepalive) clearInterval(keepalive);
      keepalive = null;
      await hud('').catch(() => {});
      running = false;
      await upd({ running: false, phase: 'idle', subreddit: '' });
    }
  })();
  return { ok: true };
}

export function stopAgent() {
  stopFlag = true;
  if (tabId != null) chrome.tabs.sendMessage(tabId, { target: 'page', cmd: 'abort' }).catch(() => {});
}

// ---------------------------------------------------------------- one-off actions (from the panel / context menu)
async function exclusive<T>(label: string, fn: () => Promise<T>): Promise<T> {
  if (running) throw new Error('The agent is busy. Stop it first.');
  running = true;
  stopFlag = false;
  keepalive = setInterval(() => chrome.runtime.getPlatformInfo().catch(() => {}), 10_000);
  try {
    return await fn();
  } catch (e) {
    if (!(e instanceof Stop)) store.log('error', `${label}: ${(e as Error).message}`);
    throw e;
  } finally {
    if (keepalive) clearInterval(keepalive);
    keepalive = null;
    await hud('').catch(() => {});
    running = false;
  }
}

export async function sendLeadNow(username: string) {
  return exclusive('Send DM', async () => {
    const lead = await getLead(username);
    if (!lead?.message) throw new Error('No message written for this lead yet.');
    if (lead.status !== 'ready') throw new Error('Approve this message before sending it.');
    const g = await checkDm();
    if (!g.ok) throw new Error(g.reason);
    await sendDm(lead);
  });
}

export async function postOpportunity(id: string) {
  return exclusive('Post comment', async () => {
    const opps = await store.get('opportunities');
    const o = opps.find((x) => x.id === id);
    if (!o) throw new Error('Opportunity not found');
    store.log('info', `Posting comment in r/${o.subreddit}`);
    await navigate(o.postUrl);
    await hud(`🕊 Commenting in <b>r/${o.subreddit}</b>`);
    const r = await cmd<{ ok: boolean; error?: string }>({ cmd: 'postComment', text: o.draft }, 180_000);
    await store.set('opportunities', (await store.get('opportunities')).map((x) => (x.id === id ? { ...x, status: r.ok ? 'posted' : 'failed' } : x)));
    if (!r.ok) throw new Error(r.error ?? 'Comment failed');
    store.log('ok', `Comment posted in r/${o.subreddit}`);
  });
}

/** Approve button on a drafted warm-up comment/post. */
export async function postWarmupItem(id: string) {
  return exclusive('Warm-up post', async () => {
    const item = (await store.get('warmupItems')).find((x) => x.id === id);
    if (!item) throw new Error('Draft not found');
    if (item.status === 'posted') throw new Error('Already posted');
    const w = await getWarmup();
    if (!w.plan) throw new Error('No warm-up plan.');
    const ok = await commitItem(warmupIo(await store.get('product'), await getSettings()), item);
    if (!ok) throw new Error((await store.get('warmupItems')).find((x) => x.id === id)?.error ?? 'Failed');
  });
}

export async function checkReplies() {
  return exclusive('Check replies', async () => {
    await navigate('https://www.reddit.com/chat');
    await wait(3000);
    const r = await cmd<{ rooms: { text: string; unread: boolean }[] }>({ cmd: 'scanInbox' }, 30_000);
    const sent = (await listLeads()).filter((l) => l.status === 'sent');
    let n = 0;
    for (const room of r.rooms ?? []) {
      if (!room.unread) continue;
      const lead = sent.find((l) => room.text.toLowerCase().includes(l.username.toLowerCase()));
      if (lead) {
        await patchLead(lead.username, { status: 'replied' });
        n++;
      }
    }
    store.log('info', `Checked chats: ${r.rooms?.length ?? 0} rooms, ${n} new repl${n === 1 ? 'y' : 'ies'} (experimental detection)`);
    return n;
  });
}

/** "Page DMer": scan whatever Reddit page is open in the active tab and queue the people on it. */
export async function scanActivePage(skipScoring: boolean): Promise<{ added: number }> {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tab?.id || !/reddit\.com/.test(tab.url ?? '')) throw new Error('Open a Reddit page in the active tab first.');
  const res: any = await chrome.tabs.sendMessage(tab.id, { target: 'page', cmd: 'scanPage' });
  const items: { username: string; subreddit: string; postUrl: string; postTitle: string; snippet: string }[] = res?.items ?? [];
  const s = await getSettings();
  let added = 0;
  if (skipScoring) {
    for (const it of items) {
      if (it.username.toLowerCase() === s.myUsername.toLowerCase() || (await isKnown(it.username))) continue;
      await putLead({ ...it, fit: 50, intent: 'unscored', reason: 'Added from page scan', status: 'queued', source: 'page', createdAt: Date.now() });
      added++;
    }
  } else {
    const product = await store.get('product');
    if (!product) throw new Error('Set up your product first, or tick "skip AI scoring".');
    const before = (await listLeads()).length;
    await ingest(product, s, items.map((it) => ({ id: it.postUrl + it.username, kind: 'post' as const, author: it.username, subreddit: it.subreddit, title: it.postTitle, text: it.snippet, postUrl: it.postUrl, postTitle: it.postTitle, source: 'page' as const })));
    added = (await listLeads()).length - before;
  }
  store.log('ok', `Page scan: ${items.length} people seen, ${added} added to queue`);
  return { added };
}

export async function scheduledTick() {
  const s = await getSettings();
  // An enabled SOP plan that is still in progress takes over scheduling: its own sessions-per-day, warm-up sessions only.
  const w = await getWarmup();
  if (w.enabled && w.plan && w.startedAt) {
    const st = planStatus(w.plan, w.startedAt, (await store.get('warmupDaily'))?.karma ?? null);
    if (st.state === 'active') {
      if (running || !inActiveHours(s)) return;
      const d = await store.getDaily();
      if (d.sessions >= w.plan.sessionsPerDay) return;
      await store.patchDaily({ sessions: d.sessions + 1 });
      store.log('info', `Scheduled warm-up session ${d.sessions + 1}/${w.plan.sessionsPerDay} (day ${st.day}, ${st.phase.name})`);
      await startAgent('warmup');
      return;
    }
  }
  if (!s.autoSchedule || running || !inActiveHours(s)) return;
  const d = await store.getDaily();
  if (d.sessions >= s.sessionsPerDay || d.pausedUntil > Date.now()) return;
  await store.patchDaily({ sessions: d.sessions + 1 });
  store.log('info', `Scheduled session ${d.sessions + 1}/${s.sessionsPerDay}`);
  await startAgent('full');
}
