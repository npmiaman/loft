// A "persona" is how THIS session behaves: typing speed, typo rate, scroll style, mouse speed, patience.
// It's stored in sessionStorage so it survives Reddit page navigations inside the tab, and is re-rolled
// after ~45 minutes. On top of it, every single action still gets its own log-normal jitter.
import { clamp, lognormal, rand } from '../rand';

export interface Persona {
  id: string;
  born: number;
  tempo: number; // overall slowness multiplier (>1 = slower)
  typingCps: number; // chars per second while typing
  typoRate: number; // chance per letter of a typo that gets corrected
  scrollSpeed: number;
  scrollChunky: boolean; // mouse-wheel ticks vs trackpad glides
  mouseSpeed: number;
  patience: number; // how long they dwell on things
  hesitation: number; // chance of a mid-move pause / re-aim
  overshoot: number; // chance of overshooting a far target then correcting
}

const KEY = 'pigeon:persona';

function make(): Persona {
  return {
    id: Math.random().toString(36).slice(2, 7),
    born: Date.now(),
    tempo: clamp(lognormal(1, 0.18), 0.72, 1.5),
    typingCps: clamp(lognormal(4.3, 0.3), 2.2, 7.5),
    typoRate: rand(0.004, 0.022),
    scrollSpeed: clamp(lognormal(1, 0.25), 0.6, 1.7),
    scrollChunky: Math.random() < 0.4,
    mouseSpeed: clamp(lognormal(1, 0.2), 0.7, 1.5),
    patience: clamp(lognormal(1, 0.3), 0.6, 1.9),
    hesitation: rand(0.04, 0.18),
    overshoot: rand(0.15, 0.45),
  };
}

function load(): Persona {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (raw) {
      const p = JSON.parse(raw) as Persona;
      if (Date.now() - p.born < 45 * 60_000) return p;
    }
  } catch { /* storage blocked */ }
  const p = make();
  try { sessionStorage.setItem(KEY, JSON.stringify(p)); } catch { /* ignore */ }
  return p;
}

export const persona: Persona = load();

/** User-controlled overall speed from Settings (1 = normal, 2 = twice as fast). */
export const userPace = { v: 1 };

const fatigue = () => 1 + Math.min(0.3, ((Date.now() - persona.born) / 3_600_000) * 0.3); // slows slightly over a session
export const tempo = () => (persona.tempo * fatigue()) / clamp(userPace.v, 0.5, 2);

/** A delay around `ms`, right-skewed, scaled by this session's tempo. */
export const t = (ms: number, sigma = 0.35) => lognormal(ms, sigma) * tempo();
