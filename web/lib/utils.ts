import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export const cn = (...v: ClassValue[]) => twMerge(clsx(v));
export const hue = (s: string) => [...s].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 0);
export const people = (n: number) => (n === 1 ? "1 person" : `${n} people`);
export const msgs = (n: number) => (n === 1 ? "1 message" : `${n} messages`);
export const when = (t: number) => new Date(t * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

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
export function friendlyError(msg: string) {
  if (/proxy not working/i.test(msg))
    return "This account's proxy isn't responding, so the browser was not opened (it would have used your own IP). Check the proxy in the ⋯ menu → Edit.";
  return msg;
}
