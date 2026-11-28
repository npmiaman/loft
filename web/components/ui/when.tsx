"use client";
import { absWhen, formatWhen } from "@/lib/utils";

/** A time: relative under a week, absolute on hover. `t` is in seconds. */
export function When({ t }: { t: number | null | undefined }) {
  if (!t) return <span className="text-mute">-</span>;
  return <time dateTime={new Date(t * 1000).toISOString()} title={absWhen(t)}>{formatWhen(t)}</time>;
}
