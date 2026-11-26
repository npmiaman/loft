// Every DM goes through here: daily cap with warm-up ramp, active hours, jittered gaps, pauses.
import { lognormal, rand, clamp } from './rand';
import { getDaily, getSettings, patchDaily } from './storage';
import type { Settings } from './types';

const RAMP = [5, 8, 12, 18, 25, 35, 45, 60]; // DMs/day by account-warm-up day

export function effectiveCap(s: Settings, now = Date.now()): number {
  if (!s.warmup) return s.dailyDmCap;
  const day = Math.max(0, Math.floor((now - (s.warmupStart || now)) / 86_400_000));
  return Math.min(s.dailyDmCap, RAMP[Math.min(day, RAMP.length - 1)] ?? 5);
}

export function inActiveHours(s: Settings, d = new Date()): boolean {
  const h = d.getHours();
  return s.activeStart < s.activeEnd ? h >= s.activeStart && h < s.activeEnd : h >= s.activeStart || h < s.activeEnd;
}

export type Gate =
  | { ok: true }
  | { ok: false; reason: string; waitMs?: number; hard?: boolean };

export async function checkDm(): Promise<Gate> {
  const [s, d] = await Promise.all([getSettings(), getDaily()]);
  const now = Date.now();
  if (d.pausedUntil > now) {
    return { ok: false, hard: true, reason: `Paused until ${new Date(d.pausedUntil).toLocaleTimeString()} (Reddit pushed back earlier)` };
  }
  if (!inActiveHours(s)) return { ok: false, hard: true, reason: `Outside active hours (${s.activeStart}:00–${s.activeEnd}:00)` };
  const cap = effectiveCap(s, now);
  if (d.count >= cap) return { ok: false, hard: true, reason: `Daily cap reached (${d.count}/${cap})` };
  if (d.nextDmAt > now) return { ok: false, reason: 'Waiting between DMs', waitMs: d.nextDmAt - now };
  return { ok: true };
}

/**
 * Gap before the next DM: right-skewed around the middle of [min,max] (many shortish gaps, a few long ones),
 * hard-clamped to a sane band, plus a ~7% chance of stepping away for a few minutes.
 */
export function nextGapMs(s: Pick<Settings, 'minGapSec' | 'maxGapSec'>): number {
  const lo = Math.max(5, s.minGapSec), hi = Math.max(lo, s.maxGapSec);
  let sec = clamp(lognormal(Math.sqrt(lo * hi), 0.45), lo * 0.7, hi * 2.5);
  if (Math.random() < 0.07) sec += rand(180, 600);
  return sec * 1000;
}

export async function recordDm(): Promise<void> {
  const [s, d] = await Promise.all([getSettings(), getDaily()]);
  await patchDaily({ count: d.count + 1, nextDmAt: Date.now() + nextGapMs(s) });
}

export const pauseFor = (ms: number) => patchDaily({ pausedUntil: Date.now() + ms });
