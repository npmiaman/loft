"use client";
import * as React from "react";
import { toast } from "sonner";
import { api } from "./api";
import type { PigeonState, Profile } from "./types";

type Ctx = {
  s: PigeonState;
  refresh: () => Promise<void>;
  /** Run an action, refresh the state, and show any error as a toast. Resolves true on success. */
  act: (fn: () => Promise<unknown>, ok?: string) => Promise<boolean>;
  canAdmin: boolean;
  profile: (id: number) => Profile | undefined;
};
const PigeonCtx = React.createContext<Ctx | null>(null);
export const usePigeon = () => React.useContext(PigeonCtx)!;

/** Polls /api/state every 3 s (and not while the window is hidden). The UI only re-renders when something actually changed. */
export function PigeonProvider({ children }: { children: (s: PigeonState | null, error: string | null, refresh: () => Promise<void>) => React.ReactNode }) {
  const [s, setS] = React.useState<PigeonState | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const last = React.useRef("");

  const refresh = React.useCallback(async () => {
    try {
      const n = await api<PigeonState>("GET", "/state");
      const j = JSON.stringify(n);
      if (j !== last.current) { last.current = j; setS(n); }
      setError(null);
    } catch (e) { setError((e as Error).message); }
  }, []);

  React.useEffect(() => {
    refresh();
    const t = setInterval(() => { if (!document.hidden) refresh(); }, 3000);
    const vis = () => { if (!document.hidden) refresh(); };
    document.addEventListener("visibilitychange", vis);
    return () => { clearInterval(t); document.removeEventListener("visibilitychange", vis); };
  }, [refresh]);

  const act = React.useCallback(async (fn: () => Promise<unknown>, ok?: string) => {
    try { await fn(); if (ok) toast.success(ok); await refresh(); return true; }
    catch (e) { toast.error((e as Error).message); await refresh(); return false; }
  }, [refresh]);

  const value = React.useMemo<Ctx | null>(() => s && ({
    s, refresh, act,
    canAdmin: s.role === "owner" || s.role === "admin",
    profile: (id: number) => s.profiles?.find((p) => p.id === id),
  }), [s, refresh, act]);

  return <PigeonCtx.Provider value={value as Ctx}>{children(s, error, refresh)}</PigeonCtx.Provider>;
}
