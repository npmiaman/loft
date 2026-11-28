// Executes a WarmupPlan: each session works through today's quotas, subreddit by subreddit, through the same human-paced
// cursor the rest of Pigeon uses. agent.ts supplies the browser plumbing (`Io`) so this file has no hidden state.
import * as store from '../storage';
import { getSettings } from '../storage';
import { clamp, lognormal, rand } from '../rand';
import type { AgentState, PostData, WarmupCounts, WarmupDaily, WarmupItem, WarmupState } from '../types';
import { draftWarmupComment, draftWarmupPost, fetchKarma, fetchRules, pickTargets, type WriteCtx } from './compile';
import { emptyQuota, lastDay, localDate, pickSub, planStatus, remainingOf, rollTargets, subsForToday, type PlanStatus, type SubTarget } from './plan';

export interface Io {
  check(): void;
  wait(ms: number): Promise<void>;
  cmd<T = any>(c: Record<string, unknown>, timeoutMs?: number): Promise<T>;
  navigate(url: string): Promise<void>;
  clickNav(hrefIncludes: string, fallbackUrl: string): Promise<boolean>;
  hud(text: string): Promise<void>;
  url(): Promise<string>;
  upd(patch: Partial<AgentState>): Promise<void>;
  bump(k: keyof AgentState['stats'], n?: number): Promise<void>;
  /** Try to send one ready DM (the governor applies the plan's DM allowance). Resolves true when DMs can't continue this session. */
  dm(): Promise<boolean>;
}

const jit = (ms: number, sigma = 0.45) => lognormal(ms, sigma);
const gap = (io: Io, ms: number) => io.wait(clamp(jit(ms, 0.5), ms * 0.4, ms * 3));

export async function getWarmup(): Promise<WarmupState> {
  return { ...store.EMPTY_WARMUP, ...(await store.get('warmup')) };
}

// ---------------------------------------------------------------- daily counters
export async function ensureDaily(st: Extract<PlanStatus, { state: 'active' }>, karma: number | null): Promise<WarmupDaily> {
  const date = localDate();
  const cur = await store.get('warmupDaily');
  if (cur && cur.date === date && cur.phase === st.phase.name) return cur;
  const next: WarmupDaily = {
    date,
    phase: st.phase.name,
    karma,
    done: cur && cur.date === date ? cur.done : emptyQuota(),
    targets: rollTargets(st.phase),
  };
  await store.set('warmupDaily', next);
  return next;
}

async function addDone(k: keyof WarmupCounts, n = 1) {
  const d = await store.get('warmupDaily');
  if (!d) return;
  await store.set('warmupDaily', { ...d, done: { ...d.done, [k]: d.done[k] + n } });
}

const pendingOf = async (kind: WarmupItem['kind']) =>
  (await store.get('warmupItems')).filter((i) => i.status === 'pending' && i.kind === kind).length;

// ---------------------------------------------------------------- publishing a drafted comment/post
export async function publishItem(io: Io, item: WarmupItem): Promise<{ ok: boolean; error?: string }> {
  if (item.kind === 'comment') {
    if (!item.postUrl) return { ok: false, error: 'No thread URL on this comment.' };
    await io.navigate(item.postUrl);
    await io.hud(`🕊 Commenting in <b>r/${item.subreddit}</b>`);
    const r = await io.cmd<{ ok: boolean; error?: string }>({ cmd: 'postComment', text: item.body }, 180_000);
    return r.ok ? { ok: true } : { ok: false, error: r.error ?? 'Comment failed' };
  }
  await io.navigate(`https://www.reddit.com/r/${encodeURIComponent(item.subreddit)}/submit/?type=TEXT`);
  await io.hud(`🕊 Posting in <b>r/${item.subreddit}</b>`);
  const r = await io.cmd<{ ok: boolean; code?: string; error?: string; errors?: string[] }>(
    { cmd: 'submitPost', title: item.title ?? '', body: item.body },
    240_000,
  );
  if (!r.ok) return { ok: false, error: [r.error, ...(r.errors ?? [])].filter(Boolean).join(' · ') || 'Post failed' };
  const end = Date.now() + 25_000; // a successful submit redirects to the new thread
  while (Date.now() < end) {
    io.check();
    if (/\/comments\//.test(await io.url().catch(() => ''))) return { ok: true };
    await new Promise((res) => setTimeout(res, 800));
  }
  const why = await io.cmd<{ errors?: string[] }>({ cmd: 'submitErrors' }, 10_000).catch(() => ({ errors: [] as string[] }));
  return { ok: false, error: why.errors?.join(' · ') || 'Reddit did not accept the post (no redirect to the new thread).' };
}

/** Publish and book the result: used by auto mode and by the Approve button. */
export async function commitItem(io: Io, item: WarmupItem): Promise<boolean> {
  const res = await publishItem(io, item);
  const items = await store.get('warmupItems');
  await store.set('warmupItems', items.map((i) => (i.id === item.id ? { ...i, status: res.ok ? 'posted' : 'failed', error: res.ok ? undefined : res.error } : i)));
  if (res.ok) {
    await addDone(item.kind);
    store.log('ok', item.kind === 'comment' ? `Comment posted in r/${item.subreddit}` : `Post published in r/${item.subreddit}: ${item.title?.slice(0, 60)}`);
  } else {
    await io.bump('errors');
    store.log('error', `${item.kind === 'comment' ? 'Comment' : 'Post'} in r/${item.subreddit} failed: ${res.error}`);
  }
  return res.ok;
}

async function enqueue(item: Omit<WarmupItem, 'id' | 'status' | 'createdAt'>): Promise<WarmupItem> {
  const full: WarmupItem = { ...item, id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`, status: 'pending', createdAt: Date.now() };
  await store.set('warmupItems', [full, ...(await store.get('warmupItems'))].slice(0, 120));
  return full;
}

// ---------------------------------------------------------------- one subreddit visit
interface Run {
  ctxFor(sub: SubTarget): WriteCtx;
  approval: WarmupState['approval'];
  rules: Map<string, string>;
  joined: Set<string>;
  postedThisSession: number;
}

async function rulesFor(run: Run, sub: string) {
  const k = sub.toLowerCase();
  if (!run.rules.has(k)) run.rules.set(k, await fetchRules(sub));
  return run.rules.get(k) ?? '';
}

/**
 * What is still worth doing today. Drafts waiting for approval count against their quota (so review mode doesn't draft forever),
 * a session writes at most one post, and the last fraction of a browsing minute doesn't keep a session alive.
 */
const left = async (run?: Run): Promise<WarmupCounts> => {
  const d = await store.get('warmupDaily');
  const r = d ? remainingOf(d.targets, d.done) : emptyQuota();
  if (r.browse < 1) r.browse = 0;
  if (run) {
    r.comment = Math.max(0, r.comment - (await pendingOf('comment')));
    r.post = run.postedThisSession >= 1 ? 0 : Math.max(0, r.post - (await pendingOf('post')));
  }
  return r;
};

async function visit(io: Io, run: Run, sub: SubTarget) {
  const ctx = run.ctxFor(sub);
  const can = (a: SubTarget['actions'][number]) => sub.actions.includes(a);
  await io.upd({ phase: 'warm-up', subreddit: sub.name });
  store.log('info', `Warm-up: r/${sub.name} (${sub.actions.join(', ')})`);
  await io.hud(`🕊 Warming up in <b>r/${sub.name}</b>`);
  await io.clickNav(`/r/${sub.name}`, `https://www.reddit.com/r/${sub.name}/`);

  // join
  if (can('join') && !run.joined.has(sub.name.toLowerCase()) && (await left(run)).join > 0) {
    const r = await io.cmd<{ ok: boolean; already?: boolean }>({ cmd: 'join' }, 60_000);
    if (r?.ok) {
      run.joined.add(sub.name.toLowerCase());
      await store.set('warmupJoined', [...run.joined]);
      if (!r.already) {
        await addDone('join');
        store.log('ok', `Joined r/${sub.name}`);
      }
      await gap(io, 2500);
    } else store.log('warn', `Couldn't find a Join button on r/${sub.name}`);
  }

  // read
  const l0 = await left(run);
  const seconds = Math.round(clamp(jit(l0.browse > 0 ? 45 : 30, 0.35), 15, 100));
  const br = await io.cmd<{ posts: PostData[] }>({ cmd: 'browse', maxPosts: 25, seconds }, 300_000);
  const posts = br.posts ?? [];
  await addDone('browse', seconds / 60);
  await io.bump('scanned', posts.length);
  if (!posts.length) {
    store.log('warn', `No posts could be read in r/${sub.name}: Reddit's markup may have changed or the page didn't load.`);
    return;
  }

  // decide
  const l1 = await left(run);
  const room = { upvote: can('upvote') ? l1.upvote : 0, comment: can('comment') ? l1.comment : 0 };
  const pick = await pickTargets(ctx, posts, Math.min(room.upvote, 1 + Math.floor(rand(0, 5))), Math.min(room.comment, 2));

  for (const id of pick.upvote) {
    io.check();
    if ((await left(run)).upvote <= 0) break;
    const p = posts.find((x) => x.id === id);
    if (!p) continue;
    const r = await io.cmd<{ ok: boolean; already?: boolean }>({ cmd: 'upvote', scope: 'post', permalink: p.permalink }, 30_000);
    if (r?.ok && !r.already) {
      await addDone('upvote');
      await io.bump('upvotes');
    }
    await gap(io, 2200);
  }

  for (const id of pick.comment) {
    io.check();
    const p = posts.find((x) => x.id === id);
    if (!p) continue;
    await io.upd({ phase: 'warm-up: reading' });
    await io.hud(`🕊 Reading <b>${p.title.slice(0, 40)}…</b>`);
    await io.clickNav(p.permalink, 'https://www.reddit.com' + p.permalink);
    const rp = await io.cmd<{ post: PostData | null; comments: { text: string; permalink: string; score: number }[] }>({ cmd: 'readPost' }, 240_000);
    const post = rp.post ?? p;
    if (can('upvote') && (await left(run)).upvote > 0) {
      const u = await io.cmd<{ ok: boolean; already?: boolean }>({ cmd: 'upvote', scope: 'post', permalink: p.permalink }, 30_000);
      if (u?.ok && !u.already) { await addDone('upvote'); await io.bump('upvotes'); }
      const best = [...(rp.comments ?? [])].sort((a, b) => b.score - a.score)[0];
      if (best && (await left(run)).upvote > 0 && Math.random() < 0.6) {
        const c = await io.cmd<{ ok: boolean; already?: boolean }>({ cmd: 'upvote', scope: 'comment', permalink: best.permalink }, 30_000);
        if (c?.ok && !c.already) { await addDone('upvote'); await io.bump('upvotes'); }
      }
    }
    const d = await draftWarmupComment(ctx, { title: post.title, body: post.body }, (rp.comments ?? []).map((c) => c.text), await rulesFor(run, sub.name));
    if (!d.comment) { store.log('info', `Nothing real to add on "${p.title.slice(0, 50)}": skipped`); continue; }
    const item = await enqueue({ kind: 'comment', subreddit: sub.name, postUrl: 'https://www.reddit.com' + p.permalink, postTitle: p.title, body: d.comment, why: d.why });
    if (run.approval === 'auto') {
      await gap(io, 4000);
      await commitItem(io, item);
    } else store.log('info', `Comment drafted for approval in r/${sub.name}`);
  }

  // a post of our own: at most one per session, only where the SOP allows it
  if (can('post')) {
    if ((await left(run)).post > 0) {
      const d = await draftWarmupPost(ctx, posts.map((p) => p.title), await rulesFor(run, sub.name));
      if (d.title && d.body) {
        run.postedThisSession++;
        const item = await enqueue({ kind: 'post', subreddit: sub.name, title: d.title, body: d.body, why: d.why });
        if (run.approval === 'auto') await commitItem(io, item);
        else store.log('info', `Post drafted for approval in r/${sub.name}: ${d.title.slice(0, 60)}`);
      }
    }
  }
}

// ---------------------------------------------------------------- session
export async function runWarmup(io: Io, sessionMinutes: number): Promise<void> {
  const s = await getSettings();
  let w = await getWarmup();
  const plan = w.plan;
  if (!plan) throw new Error('Upload your SOP and build the warm-up plan first (Warm-up tab).');
  if (!s.deepseekKey) throw new Error('Add your DeepSeek key in Settings.');
  if (!w.startedAt) {
    w = { ...w, startedAt: Date.now() };
    await store.set('warmup', w);
  }
  const karma = await fetchKarma(s.myUsername);
  if (karma === null && plan.phases.some((p) => p.minKarma)) store.log('warn', "Couldn't read the account's karma, so the plan's karma gates are being ignored this session.");
  const st = planStatus(plan, w.startedAt, karma);
  if (st.state === 'complete') throw new Error(`The warm-up plan is finished (it covered days 1–${lastDay(plan)}; today is day ${st.day}). Switch to Autopilot, or restart the plan.`);
  if (st.held) store.log('warn', st.held);
  await ensureDaily(st, karma);
  store.log('info', `Warm-up day ${st.day} · phase "${st.phase.name}"`);

  const product = await store.get('product');
  const icp = (await store.get('subreddits')).filter((x) => x.enabled).map((x) => x.name);
  const run: Run = {
    ctxFor: (sub) => ({ plan, phase: st.phase, day: st.day, product, sub }),
    approval: w.approval,
    rules: new Map(),
    joined: new Set(await store.get('warmupJoined')),
    postedThisSession: 0,
  };
  const visited = new Map<string, number>();
  const end = Date.now() + sessionMinutes * 60_000;

  const busy = (c: WarmupCounts) => c.browse + c.upvote + c.comment + c.post + c.join > 0;
  let dmDone = st.phase.quota.dm <= 0;
  while (Date.now() < end) {
    io.check();
    let l = await left(run);
    let pool = subsForToday(plan, st.day, l, icp);
    // joining is only still "to do" if some eligible subreddit hasn't been joined yet
    if (l.join > 0 && !pool.some((p) => p.actions.includes('join') && !run.joined.has(p.name.toLowerCase()))) {
      l = { ...l, join: 0 };
      pool = subsForToday(plan, st.day, l, icp);
    }
    if (busy(l)) {
      const sub = pickSub(pool, visited);
      if (!sub) {
        store.log('warn', 'No subreddit to warm up in. The plan names none yet, and no ICP subreddits are enabled (Setup tab).');
        break;
      }
      visited.set(sub.name.toLowerCase(), Date.now());
      await visit(io, run, sub);
    }
    if (!dmDone) dmDone = await io.dm();
    let l2 = await left(run);
    if (l2.join > 0 && !subsForToday(plan, st.day, l2, icp).some((p) => p.actions.includes('join') && !run.joined.has(p.name.toLowerCase()))) l2 = { ...l2, join: 0 };
    if (!busy(l2) && dmDone) {
      const d = await store.get('warmupDaily');
      const waiting = (await store.get('warmupItems')).filter((i) => i.status === 'pending').length;
      store.log('ok', `Today's warm-up is done${d ? ` (${Math.round(d.done.upvote)} upvotes, ${d.done.comment} comments, ${d.done.post} posts, ${d.done.join} joins)` : ''}${waiting ? `; ${waiting} draft${waiting === 1 ? '' : 's'} waiting for your approval` : ''}.`);
      break;
    }
    await io.upd({ phase: 'warm-up: pausing', subreddit: '' });
    await gap(io, 9_000);
  }
}
