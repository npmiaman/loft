"use client";
import { Activity, LayoutDashboard, LogOut, UserRound, Users } from "lucide-react";
import * as React from "react";
import { api } from "@/lib/api";
import { usePigeon } from "@/lib/store";
import { cn } from "@/lib/utils";
import { Select } from "./ui/field";

export type Tab = "accounts" | "team" | "activity" | "overview";
const ICONS = { accounts: UserRound, team: Users, activity: Activity, overview: LayoutDashboard };
const LABEL: Record<Tab, string> = { accounts: "Accounts", team: "Team", activity: "Activity", overview: "Overview" };

export function Shell({ tab, setTab, children }: { tab: Tab; setTab: (t: Tab) => void; children: React.ReactNode }) {
  const { s, act, canAdmin } = usePigeon();
  const tabs: Tab[] = ["accounts", "team", ...(canAdmin ? (["activity", "overview"] as Tab[]) : [])];
  return (
    <div className="grid min-h-screen grid-cols-1 md:grid-cols-[244px_1fr]">
      <aside className="flex flex-wrap items-center gap-3 border-b border-line bg-side p-3 md:sticky md:top-0 md:h-screen md:flex-col md:items-stretch md:gap-3.5 md:border-b-0 md:border-r md:p-4">
        <div className="flex items-center gap-2.5 px-2 text-[15px] font-semibold tracking-tight">
          <span className="grid size-[26px] place-items-center rounded-lg bg-ink text-sm font-bold text-on-ink">P</span> Pigeon
        </div>
        {s.me && s.me.teams.length > 0 && (
          <Select aria-label="Team" value={s.team} onChange={(e) => act(() => api("POST", "/team/" + e.target.value))} className="max-md:order-last max-md:w-48">
            {s.me.teams.map((t) => <option key={t.id} value={t.id}>{t.name} · {t.role}</option>)}
          </Select>
        )}
        <nav className="flex gap-0.5 md:flex-col">
          {tabs.map((t) => {
            const I = ICONS[t];
            return (
              <button key={t} onClick={() => setTab(t)} aria-current={tab === t ? "page" : undefined}
                className={cn("flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-left font-medium text-ink2 transition-colors hover:bg-hover", tab === t && "bg-line text-ink")}>
                <I className="size-4" aria-hidden /> {LABEL[t]}
              </button>
            );
          })}
        </nav>
        <span className="flex-1" />
        <div className="flex items-center gap-2 md:flex-col md:items-stretch md:gap-1.5 md:border-t md:border-line md:pt-3">
          <div className="min-w-0 px-1">
            <div className="truncate text-xs">{s.me?.email}</div>
            <div className="truncate text-xs text-mute">{s.device}</div>
          </div>
          <button onClick={() => act(() => api("POST", "/logout"))} className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-[13px] text-mute hover:bg-hover hover:text-ink">
            <LogOut className="size-4" aria-hidden /> Sign out
          </button>
        </div>
      </aside>
      <main className="mx-auto w-full max-w-[1160px] px-4 pb-20 pt-6 md:px-10 md:pt-8">{children}</main>
    </div>
  );
}
