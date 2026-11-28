"use client";
import * as React from "react";
import { glow } from "./glow";

/** The Pigeon mark. */
export function Brand({ className = "" }: { className?: string }) {
  return (
    <div className={`flex items-center gap-2.5 text-lg font-semibold ${className}`}>
      <span className="grid size-[26px] place-items-center rounded-md bg-ink text-sm font-bold text-on-ink">P</span> Pigeon
    </div>
  );
}

/**
 * Two-column sign-in/sign-up layout: brand panel with animated paths on the left (large screens), the form on the right.
 * The form itself is passed in as children, so the same layout serves sign-in, the first-team step and anything else that needs it.
 */
export function AuthPage({ children, tagline = "One browser per account. One proxy per browser. One shared memory for the whole team." }: { children: React.ReactNode; tagline?: string }) {
  return (
    <main className="relative lg:grid lg:h-screen lg:grid-cols-2">
      <div className="relative hidden h-full flex-col border-r border-line bg-side p-10 lg:flex">
        <div className="absolute inset-0 z-10 bg-gradient-to-t from-bg to-transparent" />
        <Brand className="z-10" />
        <div className="z-10 mt-auto">
          <p className="max-w-md text-xl font-medium leading-snug tracking-tight">{tagline}</p>
        </div>
        <div className="absolute inset-0">
          <FloatingPaths position={1} />
          <FloatingPaths position={-1} />
        </div>
      </div>
      <div className={`relative flex min-h-screen flex-col overflow-y-auto p-4 lg:h-screen lg:min-h-0 ${glow}`}>
        <div className="mx-auto my-auto w-full space-y-4 py-10 sm:w-sm">
          <Brand className="lg:hidden" />
          {children}
        </div>
      </div>
    </main>
  );
}

/**
 * Decorative lines. The paths are static; motion comes from four groups that each sweep and fade on their own timing (transform and
 * opacity only, so it stays cheap and visibly alive). An earlier version animated every path's draw-length every frame and made
 * some GPUs drop paint tiles.
 */
const TIMINGS = ["13s", "17s", "21s", "26s"];

function FloatingPaths({ position }: { position: number }) {
  const groups = React.useMemo(() => {
    const out: { id: number; d: string; width: number; opacity: number }[][] = [[], [], [], []];
    for (let i = 0; i < 36; i++) {
      out[i % 4]!.push({
        id: i,
        d: `M-${380 - i * 5 * position} -${189 + i * 6}C-${380 - i * 5 * position} -${189 + i * 6} -${312 - i * 5 * position} ${216 - i * 6} ${152 - i * 5 * position} ${343 - i * 6}C${616 - i * 5 * position} ${470 - i * 6} ${684 - i * 5 * position} ${875 - i * 6} ${684 - i * 5 * position} ${875 - i * 6}`,
        width: 0.5 + i * 0.03,
        opacity: 0.1 + i * 0.03,
      });
    }
    return out;
  }, [position]);
  return (
    <div className="pointer-events-none absolute inset-0">
      <svg className="h-full w-full text-ink" viewBox="0 0 696 316" fill="none" aria-hidden>
        {groups.map((g, gi) => (
          <g key={gi} style={{ animation: `flow ${TIMINGS[(gi + (position > 0 ? 0 : 2)) % 4]} ease-in-out ${-gi * 4}s infinite alternate` }}>
            {g.map((p) => <path key={p.id} d={p.d} stroke="currentColor" strokeWidth={p.width} strokeOpacity={p.opacity} />)}
          </g>
        ))}
      </svg>
    </div>
  );
}
