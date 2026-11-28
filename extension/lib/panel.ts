// Pure helpers behind the side panel (lists, validation, first-run steps). No browser access: unit-tested.
import type { Lead, LeadStatus, Product, Settings, Subreddit } from './types';

// ---- leads list
export interface LeadView {
  status: LeadStatus | 'all';
  q: string;
  sort: 'fit' | 'newest';
  limit: number;
}
export function viewLeads<L extends Pick<Lead, 'username' | 'fit' | 'createdAt' | 'status' | 'subreddit' | 'reason' | 'postTitle'> & { message?: string }>(
  leads: L[],
  v: LeadView,
): { shown: L[]; matching: number; total: number; label: string } {
  const q = v.q.trim().toLowerCase();
  const hit = (l: L) => !q || [l.username, l.subreddit, l.reason, l.postTitle, l.message ?? ''].some((x) => (x ?? '').toLowerCase().includes(q));
  const all = leads.filter((l) => (v.status === 'all' || l.status === v.status) && hit(l));
  all.sort(v.sort === 'newest' ? (a, b) => b.createdAt - a.createdAt : (a, b) => b.fit - a.fit || b.createdAt - a.createdAt);
  const shown = all.slice(0, v.limit);
  return { shown, matching: all.length, total: leads.length, label: `Showing ${shown.length} of ${all.length}` };
}

// ---- numbers typed into settings
/** The number in `text`, or null while it's empty/invalid/out of range (so typing never writes a 0). */
export function parseNum(text: string, o: { min?: number; max?: number; int?: boolean } = {}): number | null {
  if (!text.trim()) return null;
  const n = Number(text);
  if (!Number.isFinite(n)) return null;
  if (o.int && !Number.isInteger(n)) return null;
  if (o.min != null && n < o.min) return null;
  if (o.max != null && n > o.max) return null;
  return n;
}

export type SettingErrors = Partial<Record<keyof Settings, string>>;
export function validateSettings(s: Settings): SettingErrors {
  const e: SettingErrors = {};
  const hour = (v: number) => Number.isInteger(v) && v >= 0 && v <= 23;
  if (!hour(s.activeStart)) e.activeStart = 'Use an hour between 0 and 23.';
  if (!hour(s.activeEnd)) e.activeEnd = 'Use an hour between 0 and 23.';
  else if (s.activeStart === s.activeEnd) e.activeEnd = 'Start and end hours must differ.';
  if (!(s.minGapSec >= 10)) e.minGapSec = 'At least 10 seconds.';
  if (s.maxGapSec < s.minGapSec) e.maxGapSec = 'Must be at least the min gap.';
  if (!(s.dailyDmCap >= 1)) e.dailyDmCap = 'At least 1.';
  return e;
}

export function keyCheckMessage(r: { ok: boolean; status?: number; error?: string }): { kind: 'success' | 'error'; text: string } {
  if (r.ok) return { kind: 'success', text: 'Key works. DeepSeek answered.' };
  if (r.status === 401) return { kind: 'error', text: 'DeepSeek rejected this key. Check it was copied in full.' };
  if (r.status === 402) return { kind: 'error', text: 'The key works, but the DeepSeek account has no balance. Top it up at platform.deepseek.com.' };
  return { kind: 'error', text: `Couldn't reach DeepSeek${r.error ? `: ${r.error}` : ''}. Try again.` };
}

// ---- first run
export interface Step {
  id: 'key' | 'product' | 'subreddits' | 'safety';
  label: string;
  done: boolean;
}
export function setupSteps(s: Settings, product: Pick<Product, 'name'> | null, subs: Pick<Subreddit, 'enabled'>[], safetyConfirmed: boolean): Step[] {
  return [
    { id: 'key', label: 'Add your DeepSeek API key', done: !!s.deepseekKey },
    { id: 'product', label: 'Describe your product', done: !!product },
    { id: 'subreddits', label: 'Pick subreddits', done: subs.some((x) => x.enabled) },
    { id: 'safety', label: 'Check safety limits and your Reddit username', done: safetyConfirmed && !!s.myUsername },
  ];
}
