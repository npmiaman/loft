// Timing + randomness helpers so actions don't look machine-regular. Also the abort switch.
export class AbortError extends Error {
  constructor() {
    super('aborted');
  }
}
let aborted = false;
export const setAborted = (v: boolean) => (aborted = v);
export const isAborted = () => aborted;

import { clamp, gauss, lognormal, rand } from '../rand';
import { persona, t, tempo } from './persona';
export { clamp, gauss, lognormal, rand };
export const pick = <T,>(arr: T[]): T => arr[Math.floor(Math.random() * arr.length)] as T;

/** Abortable sleep, checked every 100ms. */
export async function sleep(ms: number): Promise<void> {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (aborted) throw new AbortError();
    await new Promise((r) => setTimeout(r, Math.min(100, Math.max(0, end - Date.now()))));
  }
  if (aborted) throw new AbortError();
}

/** rAF with a timer fallback (rAF is paused in hidden tabs). */
export const frame = () =>
  new Promise<void>((resolve) => {
    let done = false;
    const f = () => {
      if (!done) {
        done = true;
        resolve();
      }
    };
    requestAnimationFrame(f);
    setTimeout(f, 34);
  });

const easings = [
  (x: number) => 1 - Math.pow(1 - x, 3), // ease-out cubic (flick that decays)
  (x: number) => 1 - Math.pow(1 - x, 4), // ease-out quart
  (x: number) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2), // ease-in-out
];

async function animateScroll(delta: number, dur: number, ease: (x: number) => number) {
  const start = performance.now();
  let last = 0;
  for (;;) {
    if (aborted) throw new AbortError();
    const x = clamp((performance.now() - start) / dur, 0, 1);
    const pos = delta * ease(x);
    window.scrollBy(0, pos - last);
    last = pos;
    if (x >= 1) break;
    await frame();
  }
}

/**
 * Scroll the way a person does, and differently each time: wheel ticks for some sessions, trackpad glides for
 * others; irregular flick sizes, easings and gaps; the odd long pause and small scroll-back.
 */
export async function smoothScrollBy(dy: number): Promise<void> {
  const dir = Math.sign(dy) || 1;
  let remaining = Math.abs(dy);
  const speed = persona.scrollSpeed / Math.max(0.6, tempo());

  if (persona.scrollChunky) {
    while (remaining > 8) {
      const tick = Math.min(remaining, lognormal(100, 0.28));
      await animateScroll(dir * tick, clamp(lognormal(95, 0.3) / speed, 40, 260), easings[2]!);
      remaining -= tick;
      await sleep(Math.max(8, lognormal(32, 0.55) * tempo()));
      if (Math.random() < 0.06) await sleep(t(420, 0.5)); // glance at something
    }
  } else {
    while (remaining > 8) {
      const part = Math.min(remaining, Math.max(60, lognormal(Math.max(160, Math.abs(dy) / 2.2), 0.45)));
      const ease = easings[Math.floor(Math.random() * easings.length)]!;
      await animateScroll(dir * part, clamp((part * lognormal(1.15, 0.3)) / speed, 200, 1400), ease);
      remaining -= part;
      if (remaining > 8) await sleep(Math.max(30, t(190, 0.6)));
    }
  }
  if (Math.random() < 0.08 && Math.abs(dy) > 250) {
    // overshot what I wanted to read: drift back a little
    await sleep(t(260, 0.4));
    await animateScroll(-dir * Math.abs(dy) * rand(0.04, 0.12), clamp(lognormal(380, 0.3) / speed, 200, 800), easings[0]!);
  }
}

export async function waitFor<T>(fn: () => T | null | undefined | false, timeoutMs: number, every = 250): Promise<T | null> {
  const end = Date.now() + timeoutMs;
  for (;;) {
    const v = fn();
    if (v) return v as T;
    if (Date.now() > end) return null;
    await sleep(every);
  }
}
