// Pure logic for SOP-driven warm-up: validating a compiled plan, working out which phase today is, rolling daily targets.
// No browser or storage access in here so it can be unit-tested.
import type { WarmupAction, WarmupCounts, WarmupPhase, WarmupPlan, WarmupQuota, WarmupSub } from '../types';

const DAY = 86_400_000;
export const ACTIONS: WarmupAction[] = ['browse', 'upvote', 'comment', 'post', 'join', 'dm'];
export const QUOTA_KEYS = ['browse', 'upvote', 'comment', 'post', 'join', 'dm'] as const;

/**
 * Safety ceilings per day. An SOP can ask for more; Pigeon will not do it, and says so in plan.warnings.
 * These are Pigeon's own limits, not Reddit's: they keep a fresh account looking like a person.
 */
export const CEILING: WarmupQuota = { browse: 180, upvote: 80, comment: 20, post: 4, join: 10, dm: 100 };

export const emptyQuota = (): WarmupQuota => ({ browse: 0, upvote: 0, comment: 0, post: 0, join: 0, dm: 0 });

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const str = (v: unknown, max = 600) => (typeof v === 'string' ? v.trim().slice(0, max) : '');
const strs = (v: unknown, n = 30, max = 400) =>
  (Array.isArray(v) ? v : []).map((x) => str(x, max)).filter(Boolean).slice(0, n);
const int = (v: unknown, d: number) => {
  const n = typeof v === 'string' ? Number.parseFloat(v) : (v as number);
  return Number.isFinite(n) ? Math.round(n) : d;
};
const SUB_RE = /^[A-Za-z0-9_]{2,21}$/;
export const cleanSub = (s: unknown) => str(s, 60).replace(/^\/?r\//i, '').replace(/\/+$/, '');

/** Turn whatever the model returned into a plan Pigeon can run. Throws if there is nothing runnable. */
export function normalizePlan(raw: unknown, now = Date.now()): WarmupPlan {
  if (!isObj(raw)) throw new Error('The plan came back in an unexpected shape.');
  const warnings = strs(raw.warnings, 20);

  // phases: clamp numbers, drop empties, sort, then make them contiguous so every day maps to exactly one phase
  let phases: WarmupPhase[] = (Array.isArray(raw.phases) ? raw.phases : []).filter(isObj).map((p, i) => {
    const q = isObj(p.quota) ? p.quota : {};
    const quota = emptyQuota();
    for (const k of QUOTA_KEYS) {
      const want = Math.max(0, int(q[k], 0));
      const cap = CEILING[k];
      quota[k] = Math.min(want, cap);
      if (want > cap) warnings.push(`"${str(p.name, 60) || `Phase ${i + 1}`}": SOP asked for ${want} ${k}${k === 'browse' ? ' minutes' : 's'}/day; Pigeon caps it at ${cap}.`);
    }
    const from = Math.max(1, int(p.fromDay, 1));
    return {
      name: str(p.name, 60) || `Phase ${i + 1}`,
      fromDay: from,
      toDay: Math.max(from, int(p.toDay, from)),
      goal: str(p.goal, 300),
      quota,
      minKarma: Math.max(0, int(p.minKarma, 0)) || undefined,
      notes: str(p.notes, 600),
    };
  });
  phases = phases.filter((p) => QUOTA_KEYS.some((k) => p.quota[k] > 0)).sort((a, b) => a.fromDay - b.fromDay);
  if (!phases.length) throw new Error('No phase with any activity in it was found. Is this document an account warm-up SOP?');
  // contiguous from day 1: a later phase starts the day after the previous one ends; overlaps are trimmed; the first starts at day 1
  phases.forEach((p, i) => {
    const prev = phases[i - 1];
    if (i === 0) {
      if (p.fromDay > 1) warnings.push(`The SOP's first phase starts on day ${p.fromDay}; days 1–${p.fromDay - 1} are covered by that phase too.`);
      p.fromDay = 1;
    } else if (prev) {
      if (p.fromDay > prev.toDay + 1) {
        warnings.push(`Days ${prev.toDay + 1}–${p.fromDay - 1} weren't covered by any phase; "${prev.name}" now runs through them.`);
        prev.toDay = p.fromDay - 1; // gap: extend the previous phase over it
      } else if (p.fromDay <= prev.toDay) {
        warnings.push(`"${prev.name}" and "${p.name}" overlap; "${prev.name}" keeps the shared days, so "${p.name}" starts on day ${prev.toDay + 1}.`);
        p.fromDay = prev.toDay + 1; // overlap: the earlier phase wins
      }
    }
    p.toDay = Math.max(p.toDay, p.fromDay);
  });

  const subreddits: WarmupSub[] = [];
  for (const s of Array.isArray(raw.subreddits) ? raw.subreddits : []) {
    if (!isObj(s)) continue;
    const name = cleanSub(s.name);
    if (!SUB_RE.test(name) || subreddits.some((x) => x.name.toLowerCase() === name.toLowerCase())) continue;
    subreddits.push({
      name,
      fromDay: Math.max(1, int(s.fromDay, 1)),
      actions: [...new Set(strs(s.actions, 10, 20).map((a) => a.toLowerCase()).filter((a): a is WarmupAction => (ACTIONS as string[]).includes(a)))],
      notes: str(s.notes, 400),
    });
  }

  return {
    name: str(raw.name, 80) || 'Warm-up plan',
    summary: str(raw.summary, 500),
    phases,
    subreddits: subreddits.slice(0, 40),
    voice: str(raw.voice, 800),
    rules: strs(raw.rules, 30),
    avoid: strs(raw.avoid, 30),
    sessionsPerDay: Math.min(6, Math.max(1, int(raw.sessionsPerDay, 2))),
    manualSteps: strs(raw.manualSteps, 30),
    assumptions: strs(raw.assumptions, 20),
    warnings: [...new Set(warnings)],
    builtAt: now,
  };
}

const midnight = (t: number) => {
  const d = new Date(t);
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
};

/** Local calendar date, the same clock the daily counters roll over on. */
export const localDate = (now = Date.now()) => {
  const d = new Date(now);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

/** Account day 1 is the calendar day warm-up started; the day number ticks over at local midnight. */
export const accountDay = (startedAt: number, now = Date.now()) =>
  Math.max(1, Math.round((midnight(now) - midnight(startedAt || now)) / DAY) + 1);

export const startedAtForDay = (day: number, now = Date.now()) => {
  const d = new Date(now);
  d.setDate(d.getDate() - (Math.max(1, day) - 1));
  return d.getTime();
};

export const lastDay = (plan: WarmupPlan) => plan.phases[plan.phases.length - 1]?.toDay ?? 0;

export type PlanStatus =
  | { state: 'active'; day: number; phase: WarmupPhase; index: number; held?: string }
  | { state: 'complete'; day: number };

/**
 * Which phase is today? If a phase requires karma the account doesn't have yet, the account stays in the previous phase
 * (and `held` says why). karma === null means "couldn't read it": gates are then ignored, not guessed.
 */
export function planStatus(plan: WarmupPlan, startedAt: number, karma: number | null, now = Date.now()): PlanStatus {
  const day = accountDay(startedAt, now);
  let i = plan.phases.findIndex((p) => day >= p.fromDay && day <= p.toDay);
  if (i < 0) return { state: 'complete', day };
  let held: string | undefined;
  while (i > 0 && karma !== null && (plan.phases[i]?.minKarma ?? 0) > karma) {
    held = `Held in "${plan.phases[i - 1]?.name}" until ${plan.phases[i]?.minKarma} karma (account has ${karma}).`;
    i--;
  }
  return { state: 'active', day, phase: plan.phases[i] as WarmupPhase, index: i, held };
}

/** Today's goal for each action: a random 60–100% of the ceiling, so no two days look alike. */
export function rollTargets(phase: WarmupPhase, rng: () => number = Math.random): WarmupCounts {
  const t = emptyQuota();
  for (const k of QUOTA_KEYS) {
    const max = phase.quota[k];
    t[k] = max <= 0 ? 0 : Math.max(1, Math.round(max * (0.6 + 0.4 * rng())));
  }
  return t;
}

export const remainingOf = (targets: WarmupCounts, done: WarmupCounts): WarmupCounts => {
  const r = emptyQuota();
  for (const k of QUOTA_KEYS) r[k] = Math.max(0, targets[k] - done[k]);
  return r;
};

export interface SubTarget {
  name: string;
  actions: WarmupAction[]; // already intersected with what is still allowed today
  notes: string;
}

/**
 * Where to act today. If the SOP names subreddits, those (once their start day has come) are the pool; otherwise fall back to the
 * user's ICP subreddits. An action is allowed in a sub only if the sub's list (empty = anything) AND today's remaining quota allow it.
 */
export function subsForToday(plan: WarmupPlan, day: number, left: WarmupCounts, icp: string[]): SubTarget[] {
  const live = (left.browse > 0 ? ['browse'] : []).concat(
    (['upvote', 'comment', 'post', 'join'] as const).filter((a) => left[a] > 0),
  ) as WarmupAction[];
  const named = plan.subreddits.filter((s) => s.fromDay <= day);
  const pool = named.length
    ? named
    : icp.map((name): WarmupSub => ({ name, fromDay: 1, actions: [], notes: '' }));
  return pool
    .map((s) => ({
      name: s.name,
      notes: s.notes,
      actions: s.actions.length ? live.filter((a) => s.actions.includes(a)) : live,
    }))
    .filter((s) => s.actions.length > 0);
}

/** Prefer the least recently visited, with a little randomness. */
export function pickSub<T extends { name: string }>(pool: T[], visited: Map<string, number>, rng: () => number = Math.random): T | null {
  if (!pool.length) return null;
  const ranked = [...pool].sort((a, b) => (visited.get(a.name.toLowerCase()) ?? 0) - (visited.get(b.name.toLowerCase()) ?? 0));
  const top = ranked.slice(0, Math.min(3, ranked.length));
  return top[Math.floor(rng() * top.length)] ?? null;
}

/** The part of the plan that goes into every writing prompt. */
export function planBrief(plan: WarmupPlan, phase: WarmupPhase, day: number, sub?: SubTarget): string {
  const lines = [
    `WARM-UP PLAN: ${plan.name} — today is account day ${day}, phase "${phase.name}" (${phase.goal || 'no stated goal'}).`,
    phase.notes && `PHASE NOTES: ${phase.notes}`,
    plan.voice && `VOICE: ${plan.voice}`,
    plan.rules.length && `RULES (from the SOP, must obey): ${plan.rules.join(' | ')}`,
    plan.avoid.length && `NEVER: ${plan.avoid.join(' | ')}`,
    sub?.notes && `NOTES FOR r/${sub.name}: ${sub.notes}`,
  ];
  return lines.filter(Boolean).join('\n');
}

export function describeQuota(q: WarmupQuota): string {
  const parts: string[] = [];
  if (q.browse) parts.push(`${q.browse} min browsing`);
  if (q.upvote) parts.push(`${q.upvote} upvotes`);
  if (q.join) parts.push(`${q.join} joins`);
  if (q.comment) parts.push(`${q.comment} comments`);
  if (q.post) parts.push(`${q.post} posts`);
  if (q.dm) parts.push(`${q.dm} DMs`);
  return parts.join(' · ') || 'nothing';
}

/**
 * DM allowance while a plan is running, or null when the plan doesn't govern DMs (not enabled, no plan, or already complete).
 * Replaces the built-in day-by-day ramp: the SOP's own numbers are the ramp.
 */
export function dmLimit(
  w: { enabled: boolean; startedAt: number; plan: WarmupPlan | null },
  karma: number | null,
  now = Date.now(),
): { limit: number; phase: string; day: number } | null {
  if (!w.enabled || !w.plan || !w.startedAt) return null;
  const st = planStatus(w.plan, w.startedAt, karma, now);
  return st.state === 'active' ? { limit: st.phase.quota.dm, phase: st.phase.name, day: st.day } : null;
}
