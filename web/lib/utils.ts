import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export const cn = (...v: ClassValue[]) => twMerge(clsx(v));
export const hue = (s: string) => [...s].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 0);
export const people = (n: number) => (n === 1 ? "1 person" : `${n} people`);
export const msgs = (n: number) => (n === 1 ? "1 message" : `${n} messages`);
/** Absolute date and time, for hovers and anything older than a week. */
export const absWhen = (t: number) => new Date(t * 1000).toLocaleString([], { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
/** The one date format: relative under 7 days ("5 minutes ago", "yesterday"), a short date after that. `t` is in seconds. */
export function formatWhen(t: number, now = Date.now() / 1000): string {
  const d = Math.max(0, now - t);
  if (d < 45) return "just now";
  const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"} ago`;
  if (d < 3600) return plural(Math.round(d / 60) || 1, "minute");
  if (d < 86400) return plural(Math.round(d / 3600), "hour");
  if (d < 2 * 86400) return "yesterday";
  if (d < 7 * 86400) return plural(Math.floor(d / 86400), "day");
  const dt = new Date(t * 1000), same = dt.getFullYear() === new Date(now * 1000).getFullYear();
  return dt.toLocaleDateString([], same ? { month: "short", day: "numeric" } : { year: "numeric", month: "short", day: "numeric" });
}

const MODE_WORDS: Record<string, string> = { find: "finding people", dm: "sending approved messages", full: "finding people and messaging them" };
const JOB_WORDS: Record<string, string> = {
  scan: "adding the people on the page", replies: "checking for replies", doctor: "checking Reddit works", send: "sending a message",
  post: "posting a comment", submit: "posting", setup: "reading your product", discover: "finding subreddits",
};
export function pigeonWords(pg: { state: string; job: string | null; mode: string | null }) {
  if (pg.state === "stopping") return "Agent is stopping…";
  if (pg.state === "idle") return "Agent is off";
  const w = pg.job === "run" ? MODE_WORDS[pg.mode ?? ""] : JOB_WORDS[pg.job ?? ""];
  return "Agent is " + (w || "working");
}
/** What a run in this mode will do, in a sentence, from the agent's status (approved count, today's count and cap). */
export function modePlan(mode: string, st?: { leads?: Record<string, number>; daily?: { count: number }; cap?: number } | null) {
  if (mode === "find") return "Finds people and drafts messages for your approval. Sends nothing.";
  const ready = st?.leads?.ready ?? 0;
  let send: string;
  if (!st) send = "Sends approved messages, up to the daily limit.";
  else if (!ready) send = "Nothing is approved yet, so nothing will be sent.";
  else if (st.cap == null) send = `Sends up to ${ready} approved message${ready === 1 ? "" : "s"}, within the daily limit.`;
  else {
    const n = Math.max(0, Math.min(ready, st.cap - (st.daily?.count ?? 0)));
    send = n ? `Sends up to ${n} approved message${n === 1 ? "" : "s"} today.` : "Today's limit is reached, so nothing more will be sent today.";
  }
  return mode === "full" ? `Finds people and drafts messages for your approval. ${send}` : send;
}

export function friendlyError(msg: string) {
  if (/proxy not working/i.test(msg))
    return "This account's proxy isn't responding, so the browser was not opened (it would have used your own IP). Check the proxy: More, then Edit.";
  return msg;
}

export const OS_NAMES: Record<string, string> = { windows: "Windows", macos: "macOS", linux: "Linux" };
export const osName = (o: string) => OS_NAMES[o] ?? o;

/** Is this account signed in to Reddit, in words for the account list. */
export function redditStatus(p: { version: number; reddit?: { signed_in: boolean | null; user: string | null } }) {
  const r = p.reddit ?? { signed_in: null, user: null };
  if (r.signed_in === false || (r.signed_in === null && !r.user && !p.version))
    return { state: "disconnected" as const, text: "Reddit not connected", title: "Open the browser and sign in to Reddit, then Close & save." };
  if (r.user) return { state: "connected" as const, text: `u/${r.user}`, title: "Signed in to Reddit." };
  if (r.signed_in) return { state: "connected" as const, text: "Reddit connected", title: "Signed in. The username shows once the agent has run." };
  return { state: "unknown" as const, text: "Not checked on this computer", title: "This computer has no copy of the account yet. Open it here once to check." };
}
