"use client";
import { ArrowRight, Sparkles, X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import * as React from "react";
import { usePigeon } from "@/lib/store";
import { msgs, people, pigeonWords } from "@/lib/utils";
import { Avatar, Callout, Dot } from "../ui/bits";
import { Button } from "../ui/button";
import { Drawer, DrawerClose } from "../ui/dialog";
import { PgProvider, usePg } from "./context";
import { Comments, Messages, People, Product, Run, SettingsTab, TeamActivity } from "./tabs";

const TABS = [["leads", "People", People], ["run", "Run", Run], ["comments", "Comments", Comments], ["messages", "Messages", Messages],
              ["setup", "Product", Product], ["settings", "Settings", SettingsTab], ["brain", "Team activity", TeamActivity]] as const;

/** The one thing to do next, in a sentence. */
function Next({ go }: { go: (t: string) => void }) {
  const { st, busy } = usePg();
  if (!st) return null;
  const n = (k: string) => st.leads?.[k] ?? 0;
  let text: React.ReactNode, to: [string, string] | null = null;
  if (st.state === "stopping") text = "Stopping… the browser stays open.";
  else if (busy) text = "The agent is working in its own tab. You can keep using the browser. Nothing for you to do.";
  else if (!st.product) { text = <><b>Start here:</b> tell the agent what you're promoting, so it knows who to look for.</>; to = ["setup", "Go to Product"]; }
  else if (n("draft")) { text = <><b>{n("draft")} message{n("draft") > 1 ? "s are" : " is"} waiting for you.</b> Read them and press Approve.</>; to = ["leads", "Review messages"]; }
  else if (n("ready")) { text = <><b>{n("ready")} approved message{n("ready") > 1 ? "s are" : " is"} ready.</b> Press Start (mode: send approved messages) to send them.</>; to = ["run", "Go to Run"]; }
  else if (!st.stats.leads) { text = <><b>Next:</b> press Start to find people to message.</>; to = ["run", "Go to Run"]; }
  else text = "All caught up. Press Start on the Run tab to find more people.";
  return (
    <Callout icon={<Sparkles className="size-4 shrink-0 text-acc" aria-hidden />}
      action={to && <Button size="sm" variant="primary" onClick={() => go(to![0])}>{to[1]} <ArrowRight className="size-3.5" /></Button>}>{text}</Callout>
  );
}

function Body({ id, onClose }: { id: number; onClose: () => void }) {
  const { profile } = usePigeon(); const pg = usePg(); const p = profile(id);
  const [tab, setTab] = React.useState<string>("leads");
  const Cur = TABS.find((t) => t[0] === tab)![2];
  const st = pg.st;
  return (
    <div id="pg" className="px-7 pb-10 pt-5">
      <div className="mb-3.5 flex items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <Avatar name={p?.name ?? "?"} size={40} />
          <div>
            <h3 className="text-[17px] font-semibold tracking-tight">Agent · {p?.name ?? `account ${id}`}</h3>
            {st && (
              <div className="mt-0.5 flex flex-wrap items-center gap-x-2.5 text-[12.5px] text-mute">
                <span className="inline-flex items-center gap-1.5 font-medium text-ink"><Dot tone={st.state === "idle" ? "idle" : "ok"} /> {pigeonWords(st)}</span>
                <span>looked at {st.stats.scanned} · found {people(st.stats.leads)} · sent {msgs(st.stats.dms)} · {st.daily.count} today{st.central ? " · team history: on" : ""}</span>
              </div>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1">
          {st && st.state !== "idle" && <Button size="sm" variant="danger" onClick={() => pg.run(() => fetchStop(id), "stopping…")}>Stop</Button>}
          <DrawerClose asChild><Button size="icon" variant="ghost" aria-label="Close" onClick={onClose}><X className="size-4" /></Button></DrawerClose>
        </div>
      </div>
      {st && !st.central && <div className="mb-3 rounded-lg bg-bad-bg px-3 py-2 text-[13px] text-bad">Team history is OFF: this account can&apos;t see who your other accounts already messaged.</div>}
      {st?.log?.length ? <div className="mb-3 truncate font-mono text-xs text-mute">{st.log[st.log.length - 1].msg}</div> : null}
      {st?.last && !st.last.ok && st.last.error && <div className="mb-3 text-[13px] text-bad">{st.last.kind}: {st.last.error}</div>}
      <Next go={(t) => { setTab(t); pg.setMsg(""); }} />
      <div role="tablist" className="relative mb-5 mt-4 flex gap-0.5 overflow-x-auto border-b border-line">
        {TABS.map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => { setTab(k); pg.setMsg(""); }}
            className={`relative px-3 py-2.5 font-medium whitespace-nowrap transition-colors ${tab === k ? "text-ink" : "text-mute hover:text-ink"}`}>
            {label}
            {tab === k && <motion.span layoutId="pgtab" className="absolute inset-x-2 -bottom-px h-0.5 rounded bg-ink" transition={{ type: "spring", stiffness: 500, damping: 40 }} />}
          </button>
        ))}
      </div>
      {pg.msg && <pre className="log mb-4 whitespace-pre-wrap rounded-lg border border-line bg-hover/60 px-3 py-2 font-mono text-xs text-mute">{pg.msg}</pre>}
      <AnimatePresence mode="wait" initial={false}>
        <motion.div key={tab} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.14 }}><Cur /></motion.div>
      </AnimatePresence>
    </div>
  );
}
const fetchStop = (id: number) => import("@/lib/api").then(({ api }) => api("POST", `/profiles/${id}/pigeon/stop`));

export function PigeonDrawer({ id, onClose }: { id: number | null; onClose: () => void }) {
  return (
    <Drawer open={id != null} onOpenChange={(o) => !o && onClose()} label="Agent controls">
      {id != null && <PgProvider id={id} goto={() => {}}><Body id={id} onClose={onClose} /></PgProvider>}
    </Drawer>
  );
}
