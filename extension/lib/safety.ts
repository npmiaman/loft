// Pure safety rules shared by the governor (background) and the side panel. No browser or storage access here,
// so the panel shows exactly what the governor enforces and everything can be unit-tested.
import { dmLimit } from './sop/plan';
import { DEFAULT_SETTINGS } from './storage';
import type { Daily, Lead, LeadStatus, RunMode, Settings, WarmupPlan } from './types';

const RAMP = [5, 8, 12, 18, 25, 35, 45, 60]; // DMs/day by account day while the DM ramp is on
const DAY = 86_400_000;

/** Cap from the built-in DM ramp (new accounts), or the flat daily limit when the ramp is off. */
export function effectiveCap(s: Settings, now = Date.now()): number {
  if (!s.warmup) return s.dailyDmCap;
  return Math.min(s.dailyDmCap, RAMP[Math.min(rampDay(s, now) - 1, RAMP.length - 1)] ?? 5);
}
const rampDay = (s: Settings, now: number) => Math.max(0, Math.floor((now - (s.warmupStart || now)) / DAY)) + 1;

export function inActiveHours(s: Pick<Settings, 'activeStart' | 'activeEnd'>, d = new Date()): boolean {
  const h = d.getHours();
  return s.activeStart < s.activeEnd ? h >= s.activeStart && h < s.activeEnd : h >= s.activeStart || h < s.activeEnd;
}

export interface CapInfo {
  cap: number;
  reason: string; // short, shown next to the number: 'warm-up plan phase "Lurk" (day 2)'
  source: 'plan' | 'ramp' | 'limit';
  phase?: string;
  day?: number;
}

type WarmupLike = { enabled: boolean; startedAt: number; plan: WarmupPlan | null } | null | undefined;

/** THE daily DM cap. The governor enforces it and the panel displays it, so they can never disagree. */
export function dmCap(s: Settings, w: WarmupLike, karma: number | null, now = Date.now()): CapInfo {
  const plan = w ? dmLimit(w, karma, now) : null;
  if (plan) {
    return { cap: Math.max(0, Math.min(s.dailyDmCap, plan.limit)), reason: `warm-up plan phase "${plan.phase}" (day ${plan.day})`, source: 'plan', phase: plan.phase, day: plan.day };
  }
  if (s.warmup) {
    const cap = effectiveCap(s, now);
    if (cap < s.dailyDmCap) return { cap, reason: `DM ramp, day ${rampDay(s, now)}`, source: 'ramp', day: rampDay(s, now) };
  }
  return { cap: s.dailyDmCap, reason: 'your daily limit', source: 'limit' };
}

const pad = (n: number) => String(n).padStart(2, '0');
const midnight = (t: number) => { const d = new Date(t); return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime(); };
const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "15:05" today, "tomorrow 10:42", otherwise "Wed 2 Dec 08:00". */
export function formatUntil(ts: number, now = Date.now()): string {
  const d = new Date(ts);
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const days = Math.round((midnight(ts) - midnight(now)) / DAY);
  if (days === 0) return hm;
  if (days === 1) return `tomorrow ${hm}`;
  return `${DOW[d.getDay()]} ${d.getDate()} ${MON[d.getMonth()]} ${hm}`;
}

export interface DmBlock {
  kind: 'paused' | 'plan' | 'cap' | 'hours';
  text: string;
  until?: number;
  canResume: boolean; // a pause can be lifted by the user; a plan/cap/hours limit can't from here
}

/** Why DMs won't go out right now, in the order the governor checks them, or null if nothing blocks them. */
export function dmBlock(s: Settings, d: Pick<Daily, 'count' | 'pausedUntil' | 'pauseReason' | 'date'>, cap: CapInfo, now = Date.now()): DmBlock | null {
  if (d.pausedUntil > now) {
    return { kind: 'paused', until: d.pausedUntil, canResume: true, text: `DMs paused until ${formatUntil(d.pausedUntil, now)}${d.pauseReason ? `: ${d.pauseReason}` : ''}` };
  }
  if (!inActiveHours(s, new Date(now))) return { kind: 'hours', canResume: false, text: `Outside your active hours (${s.activeStart}:00 to ${s.activeEnd}:00)` };
  if (cap.source === 'plan' && cap.cap <= 0) return { kind: 'plan', canResume: false, text: `No DMs today: ${cap.reason} allows none` };
  if (d.count >= cap.cap) return { kind: 'cap', canResume: false, text: `Today's limit reached (${d.count} of ${cap.cap}, ${cap.reason})` };
  return null;
}

/** Status a freshly written message gets. Every DM waits for approval, whoever wrote it (AI, template, import, page scan). */
export const WRITTEN_STATUS: LeadStatus = 'draft';

/** Load settings from storage safely: fill new defaults, drop retired keys. */
export function migrateSettings(raw: Partial<Settings> | undefined | null): Settings {
  const { approveAiDrafts: _gone, ...rest } = { ...DEFAULT_SETTINGS, ...(raw ?? {}) } as Settings & { approveAiDrafts?: boolean };
  return rest;
}

const plural = (n: number, one: string, many = one + 's') => `${n} ${n === 1 ? one : many}`;

/** One line next to Start that says what this run will actually send. */
export function startSummary(mode: RunMode, x: { cap: number; sent: number; ready: number; block: DmBlock | null }): string {
  if (mode === 'find') return 'Will not send anything. New leads go to Review.';
  if (x.block) return `Will not send any messages: ${x.block.text}.`;
  const n = Math.max(0, Math.min(x.ready, x.cap - x.sent));
  const base = `Will send up to ${plural(n, 'approved message')} today (limit ${x.cap}, ${x.sent} sent).`;
  if (mode === 'warmup') return `Runs today's warm-up plan. ${base}`;
  return mode === 'full' ? `${base} New messages wait for your approval.` : base;
}

// ---- never message the same person twice
const CONTACTED: LeadStatus[] = ['sent', 'replied'];
export const wasContacted = (l: Pick<Lead, 'status'> & { sentAt?: number }) => CONTACTED.includes(l.status) || !!l.sentAt;

/** The contacted set after leads are cleared: everything already in it plus everyone the cleared leads had reached. */
export function contactedAfterClear(contacted: string[], leads: Pick<Lead, 'username' | 'status'>[]): Set<string> {
  const s = new Set(contacted.map((x) => x.toLowerCase()));
  for (const l of leads) if (wasContacted(l)) s.add(l.username.toLowerCase());
  return s;
}
export const isContacted = (set: Set<string> | string[], name: string) =>
  (set instanceof Set ? set : new Set(set.map((x) => x.toLowerCase()))).has(name.toLowerCase());

/** Storage keys "Clear unsent leads" removes: everything except people already messaged. */
export const unsentLeadKeys = (leads: Pick<Lead, 'username' | 'status'>[]) =>
  leads.filter((l) => !wasContacted(l)).map((l) => 'lead:' + l.username.toLowerCase());
