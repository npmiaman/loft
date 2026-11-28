"use client";
import { ChevronsUpDown, LayoutDashboard, LogOut, Menu as MenuIcon, Settings, UserRound, X } from "lucide-react";
import * as React from "react";
import { api } from "@/lib/api";
import { usePigeon } from "@/lib/store";
import { cn } from "@/lib/utils";
import { Avatar } from "./ui/bits";
import { IconButton } from "./ui/button";
import { Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger } from "./ui/dropdown";
import { Select } from "./ui/field";
import { glow } from "./ui/glow";

export type Tab = "accounts" | "overview" | "settings";
/** Go to another page from anywhere (e.g. the Share dialog's "Invite someone"). */
export const NavCtx = React.createContext<(t: Tab, section?: string) => void>(() => {});
export const useNav = () => React.useContext(NavCtx);
const ICONS = { accounts: UserRound, overview: LayoutDashboard, settings: Settings };
const ROLE: Record<string, string> = { owner: "Owner", admin: "Admin", member: "Member" };
const LABEL: Record<Tab, string> = { accounts: "Accounts", overview: "Overview", settings: "Settings" };

const Brand = () => (
  <div className="flex items-center gap-2.5 px-2 text-lg font-semibold tracking-tight">
    <span className="grid size-[26px] place-items-center rounded-md bg-ink text-sm font-bold text-on-ink">P</span> Pigeon
  </div>
);

/** Sidebar on desktop; on phones a compact top bar whose menu holds the same things (pages, team, sign out). */
export function Shell({ tab, setTab, banner, children }: { tab: Tab; setTab: (t: Tab) => void; banner?: React.ReactNode; children: React.ReactNode }) {
  const { s, act, canAdmin } = usePigeon();
  const [open, setOpen] = React.useState(false);
  const tabs: Tab[] = ["accounts", ...(canAdmin ? (["overview"] as Tab[]) : [])]; // team activity is the Overview's history panel // Settings (incl. the team) opens from the account menu
  const go = (t: Tab) => { setTab(t); setOpen(false); };

  const nav = (
    <nav aria-label="Pages" className="flex flex-col gap-0.5">
      {tabs.map((t) => {
        const I = ICONS[t];
        return (
          <button key={t} onClick={() => go(t)} aria-current={tab === t ? "page" : undefined}
            className={cn("flex items-center gap-2.5 rounded-md px-2.5 py-2 text-left font-medium text-ink2 transition-colors hover:bg-hover", tab === t && "bg-line text-ink")}>
            <I className="size-4" aria-hidden /> {LABEL[t]}
          </button>
        );
      })}
    </nav>
  );
  const teamPick = s.me && s.me.teams.length > 0 && (
    <Select aria-label="Team" value={s.team} onChange={(e) => act(() => api("POST", "/team/" + e.target.value))}>
      {s.me.teams.map((t) => <option key={t.id} value={t.id}>{t.name} · {ROLE[t.role] ?? t.role}</option>)}
    </Select>
  );
  const signOut = () => act(() => api("POST", "/logout"));
  // The account button at the bottom of the sidebar opens a small menu: Settings, Sign out.
  const who = (
    <div className="border-t border-line pt-3">
      <Menu>
        <MenuTrigger asChild>
          <button type="button" aria-label="Account menu" className="flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-hover data-[state=open]:bg-line">
            <Avatar name={s.me?.name || s.me?.email || "?"} size={28} />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium">{s.me?.name || s.me?.email}</span>
              <span className="block truncate text-xs text-mute">{s.me?.email}</span>
            </span>
            <ChevronsUpDown className="size-4 shrink-0 text-mute" aria-hidden />
          </button>
        </MenuTrigger>
        <MenuContent side="top" align="start" className="w-56">
          <MenuItem onSelect={() => go("settings")}><Settings className="size-4" aria-hidden /> Settings</MenuItem>
          <MenuSeparator />
          <MenuItem onSelect={signOut}><LogOut className="size-4" aria-hidden /> Sign out</MenuItem>
        </MenuContent>
      </Menu>
    </div>
  );

  return (
    <div className="grid min-h-screen grid-cols-1 content-start md:grid-cols-[244px_1fr] md:content-stretch">
      {/* phones: one row, brand + current page + menu */}
      <header className="sticky top-0 z-30 border-b border-line bg-side md:hidden">
        <div className="flex h-14 items-center gap-2 px-2">
          <Brand />
          <span className="min-w-0 flex-1 truncate text-sm text-mute">{LABEL[tab]}</span>
          <IconButton icon={open ? X : MenuIcon} label={open ? "Close menu" : "Menu"} aria-expanded={open} aria-controls="mobile-menu" onClick={() => setOpen(!open)} />
        </div>
        {open && <div id="mobile-menu" className="space-y-3 border-t border-line px-4 pb-4 pt-3">{nav}{teamPick}{who}</div>}
      </header>
      <aside className="hidden flex-col gap-3.5 border-r border-line bg-gradient-to-t from-bg to-side p-4 md:sticky md:top-0 md:flex md:h-screen">
        <Brand />
        {teamPick}
        {nav}
        <span className="flex-1" />
        {who}
      </aside>
      <div className={cn("min-w-0", glow)}>
        {banner}
        <main className="mx-auto w-full max-w-[1160px] px-4 pb-20 pt-6 md:px-10 md:pt-8">{children}</main>
      </div>
    </div>
  );
}
