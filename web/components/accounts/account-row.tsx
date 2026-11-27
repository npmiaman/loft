"use client";
import { AlertTriangle, Globe, MoreHorizontal, Play, Square } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import * as React from "react";
import { api } from "@/lib/api";
import { useLoft } from "@/lib/store";
import type { Job, Profile } from "@/lib/types";
import { friendlyError, msgs, people, pigeonWords } from "@/lib/utils";
import { Avatar, Dot, Pill } from "../ui/bits";
import { Button } from "../ui/button";
import { Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger } from "../ui/dropdown";
import { Select } from "../ui/field";

function status(p: Profile, j?: Job) {
  if (p.running_here) return { tone: "ok" as const, text: "Open on this computer", sub: j?.pigeon ? pigeonWords(j.pigeon) : "Starting…" };
  if (p.running) return { tone: "warn" as const, text: `${p.running.by} is using it`, sub: `on ${p.running.device}` };
  const err = j && !j.alive ? j.log.find((l) => /^error:/i.test(l)) : undefined;
  if (err) return { tone: "bad" as const, text: "Couldn't open", sub: "", err: err.replace(/^error:\s*/i, "") };
  return { tone: "idle" as const, text: p.version ? "Closed · saved" : "Not opened yet", sub: "" };
}

const MODES = [["find", "Find people (sends nothing)"], ["full", "Find people + message them"], ["dm", "Send approved messages"]] as const;

export function AccountRow({ p, mode, setMode, onPigeon, onEdit, onShare, onCookies, hasRunners, canAdmin }: {
  p: Profile; mode: string; setMode: (m: string) => void; onPigeon: () => void; onEdit: () => void; onShare: () => void; onCookies: () => void;
  hasRunners: boolean; canAdmin: boolean;
}) {
  const { s, act } = useLoft();
  const j = s.jobs?.[`run-${p.id}`];
  const st = status(p, j);
  const pg = p.running_here ? j?.pigeon : null;
  const busy = !!p.running || p.running_here;
  const [raw, setRaw] = React.useState(false);

  return (
    <motion.div layout="position" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.2 }}
      className="grid items-center gap-4 border-b border-line px-5 py-4 transition-colors last:border-b-0 hover:bg-hover/45 lg:grid-cols-[minmax(230px,1.3fr)_minmax(180px,1fr)_auto]">
      <div className="flex min-w-0 items-start gap-3">
        <span className="mt-0.5"><Avatar name={p.name} /></span>
        <div className="min-w-0">
          <div className="text-[14.5px] font-semibold tracking-tight">{p.name}</div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2.5 gap-y-1">
            {p.proxy
              ? <span className="inline-flex items-center gap-1.5 text-xs text-mute"><Globe className="size-3.5" aria-hidden /> Own proxy · {p.proxy.server.replace(/^[a-z0-9]+:\/\//i, "")}</span>
              : <span className="inline-flex items-center gap-1.5 text-xs text-warn"><AlertTriangle className="size-3.5" aria-hidden /> No proxy: uses your own IP</span>}
            {p.tags.map((t) => <Pill key={t}>{t}</Pill>)}
          </div>
          {p.notes && <div className="mt-1 text-xs text-mute">{p.notes}</div>}
          {st.err && <div role="alert" className="mt-2 rounded-lg bg-bad-bg px-2.5 py-2 text-[12.5px] text-bad">{friendlyError(st.err)}</div>}
          {j && j.log.length > 0 && (
            <div className="mt-1">
              <button onClick={() => setRaw(!raw)} className="text-xs text-mute hover:text-ink">{raw ? "Hide details" : "Details"}</button>
              {raw && <pre className="mt-1 max-w-xl whitespace-pre-wrap font-mono text-xs text-mute">{j.log.join("\n")}</pre>}
            </div>
          )}
        </div>
      </div>

      <div>
        <span className="inline-flex items-center gap-2 font-medium"><Dot tone={st.tone} /> {st.text}</span>
        <AnimatePresence mode="wait" initial={false}>
          {st.sub && <motion.div key={st.sub} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="mt-0.5 pl-4 text-[12.5px] text-mute">{st.sub}</motion.div>}
        </AnimatePresence>
        {pg?.stats && <div className="pl-4 text-[12.5px] text-mute">found {people(pg.stats.leads)} · sent {msgs(pg.stats.dms)}</div>}
      </div>

      <div className="flex flex-wrap items-center gap-2 lg:justify-end">
        {!p.running_here ? (
          <Button variant="primary" disabled={busy} onClick={() => act(() => api("POST", `/profiles/${p.id}/run`))}><Play className="size-3.5 fill-current" aria-hidden /> Open browser</Button>
        ) : (
          <>
            {pg && (pg.state !== "idle"
              ? <Button onClick={() => act(() => api("POST", `/profiles/${p.id}/pigeon`, { action: "stop" }))}>Stop Pigeon</Button>
              : <>
                  <Select aria-label="What should Pigeon do?" value={mode} onChange={(e) => setMode(e.target.value)} className="w-56">
                    {MODES.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
                  </Select>
                  <Button variant="primary" onClick={() => act(() => api("POST", `/profiles/${p.id}/pigeon`, { action: "start", mode }))}>Start Pigeon</Button>
                </>)}
            {pg && <Button onClick={onPigeon}>Pigeon controls</Button>}
            <Button onClick={() => act(() => api("POST", `/profiles/${p.id}/stop`))} title="Closes the browser and saves its cookies to the team"><Square className="size-3 fill-current" aria-hidden /> Close &amp; save</Button>
          </>
        )}
        <Menu>
          <MenuTrigger asChild><Button size="icon" variant="ghost" aria-label={`More for ${p.name}`}><MoreHorizontal className="size-4" /></Button></MenuTrigger>
          <MenuContent>
            {p.access === "edit" && <><MenuItem onSelect={onEdit}>Edit</MenuItem><MenuItem onSelect={onShare}>Share</MenuItem></>}
            <MenuItem disabled={busy} onSelect={onCookies}>Import / export cookies</MenuItem>
            {hasRunners && <MenuItem disabled={busy} onSelect={() => act(async () => { const r = await api<{ url: string }>("POST", `/profiles/${p.id}/cloud`); window.open(r.url, "_blank"); })}>Run on a cloud runner</MenuItem>}
            {p.running && canAdmin && !p.running_here && (
              <><MenuSeparator /><MenuItem danger onSelect={() => { if (confirm("Force-release the lock? Unsynced changes on the other device will be rejected.")) act(() => api("POST", `/profiles/${p.id}/unlock`)); }}>Force unlock</MenuItem></>
            )}
            <MenuSeparator />
            <MenuItem onSelect={() => { window.location.href = "/legacy/"; }}>More options (classic view)</MenuItem>
          </MenuContent>
        </Menu>
      </div>
    </motion.div>
  );
}
