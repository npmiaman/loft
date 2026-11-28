// Every DM goes through here: daily cap with warm-up ramp, active hours, jittered gaps, pauses.
import { lognormal, rand, clamp } from './rand';
import { get, getDaily, getSettings, patchDaily } from './storage';
import { dmCap, dmBlock, effectiveCap, inActiveHours } from './safety';
import type { CapInfo } from './safety';
import type { Settings } from './types';

export { effectiveCap, inActiveHours };

export type Gate =
  | { ok: true }
  | { ok: false; reason: string; waitMs?: number; hard?: boolean };

/** Today's cap as the governor sees it (settings + DM ramp + any running warm-up plan). The panel shows the same thing. */
export async function currentCap(now = Date.now()): Promise<CapInfo> {
  const [s, w, wd] = await Promise.all([getSettings(), get('warmup'), get('warmupDaily')]);
  return dmCap(s, w, wd?.karma ?? null, now);
}

export async function checkDm(): Promise<Gate> {
  const [s, d] = await Promise.all([getSettings(), getDaily()]);
  const now = Date.now();
  const cap = await currentCap(now);
  const b = dmBlock(s, d, cap, now);
  if (b) {
    // keep the wording the log has always used for the plan case
    const reason = b.kind === 'plan' ? `Warm-up plan: no DMs in "${cap.phase}" (day ${cap.day})` : b.kind === 'paused' ? `${b.text} (Reddit pushed back earlier)` : b.text;
    return { ok: false, hard: true, reason };
  }
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

export const pauseFor = (ms: number, reason = '') => patchDaily({ pausedUntil: Date.now() + ms, pauseReason: reason });
/** "Resume now" on the status banner. */
export const resumeDms = () => patchDaily({ pausedUntil: 0, pauseReason: '' });
