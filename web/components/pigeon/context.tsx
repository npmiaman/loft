"use client";
import * as React from "react";
import { toast } from "sonner";
import { api, isNoAiKey } from "@/lib/api";
import type { PigeonStatus } from "@/lib/types";

export type Pg = {
  id: number; st: PigeonStatus | null; busy: boolean; tick: number; msg: string;
  op: (op: string, args?: Record<string, unknown>) => Promise<any>;
  job: (kind: string, args?: Record<string, unknown>) => Promise<any>;
  /** Run an action, show its result (or an error toast) and refresh the status. Resolves true on success. */
  run: (fn: () => Promise<any>, ok?: string) => Promise<boolean>;
  setMsg: (m: string) => void; goto: (t: string) => void;
  /** A tab with unsaved edits says so here; switching tabs or closing the drawer then asks first. */
  setDirty: (d: boolean) => void;
};
const Ctx = React.createContext<Pg | null>(null);
export const usePg = () => React.useContext(Ctx)!;

/** The backend's acknowledgements are terse CLI lines ("imported 2 username(s); 0 already in the queue"): make the first line a sentence. */
export function ack(out: unknown): string {
  if (typeof out !== "string" || !out.trim()) return "";
  let s = out.trim().split("\n")[0].trim();
  if (s.length > 160) return "Done";
  s = s.replace(/(\d+) (\w+?)\(s\)/g, (_, n: string, w: string) => `${n} ${w}${n === "1" ? "" : "s"}`).replace(/\(s\)/g, "s");
  s = s.replace(/^u\/(\S+): (\w+)$/, "u/$1 is now $2");
  return s.charAt(0).toUpperCase() + s.slice(1) + (/[.!?]$/.test(s) ? "" : ".");
}

const editing = () => ["TEXTAREA", "INPUT", "SELECT"].includes(document.activeElement?.tagName ?? "");

export function PgProvider({ id, goto, setDirty, children }: { id: number; goto: (t: string) => void; setDirty: (d: boolean) => void; children: React.ReactNode }) {
  const [st, setSt] = React.useState<PigeonStatus | null>(null);
  const [tick, setTick] = React.useState(0);
  const [msg, setMsg] = React.useState("");
  const last = React.useRef("");

  const poll = React.useCallback(async () => {
    try {
      const n = await api<PigeonStatus>("GET", `/profiles/${id}/pigeon/status`);
      const j = JSON.stringify(n);
      if (j !== last.current) { last.current = j; setSt(n); }
      if (!editing()) setTick((t) => t + 1);   // tabs refresh their lists, but never while someone is typing
    } catch { /* the session may be closing */ }
  }, [id]);
  React.useEffect(() => { poll(); const t = setInterval(poll, 2500); return () => clearInterval(t); }, [poll]);

  const op = React.useCallback((o: string, args: Record<string, unknown> = {}) => api("POST", `/profiles/${id}/pigeon/op`, { op: o, args }), [id]);
  const job = React.useCallback((kind: string, args: Record<string, unknown> = {}) => api("POST", `/profiles/${id}/pigeon/job`, { kind, args }), [id]);
  const run = React.useCallback(async (fn: () => Promise<any>, ok?: string) => {
    let done = false;
    try {
      const r = await fn(); done = true;
      const say = ok !== undefined ? ok : ack(r && r.out);
      if (say) toast.success(say);
    }
    catch (e) {
      const m = (e as Error).message;
      if (isNoAiKey(m)) toast.error(m, { action: { label: "Add AI key", onClick: () => goto("settings") } });
      else toast.error(m);
    }
    await poll(); setTick((t) => t + 1);
    return done;
  }, [poll, goto]);

  const value = React.useMemo<Pg>(() => ({ id, st, busy: !!st && st.state !== "idle", tick, msg, op, job, run, setMsg, goto, setDirty }), [id, st, tick, msg, op, job, run, goto, setDirty]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/** Load a tab's data now, after every action, and on each status tick (unless someone is typing). */
export function useData<T>(load: (pg: Pg) => Promise<T>, deps: unknown[] = []) {
  const pg = usePg(); const [d, setD] = React.useState<T | null>(null);
  const loadRef = React.useRef(load); loadRef.current = load;
  React.useEffect(() => { let live = true; loadRef.current(pg).then((x) => live && setD(x)).catch(() => {}); return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pg.tick, ...deps]);
  return d;
}
