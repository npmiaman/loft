"use client";
import { ArrowRight, Sparkles, X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import * as React from "react";
import { friendly, isNoAiKey } from "@/lib/api";
import { usePigeon } from "@/lib/store";
import { msgs, people, pigeonWords } from "@/lib/utils";
import { Avatar, Callout, Dot } from "../ui/bits";
import { Button, IconButton } from "../ui/button";
import { Select } from "../ui/field";
import { useConfirm } from "../ui/confirm";
import { Drawer } from "../ui/dialog";
import { PgProvider, usePg } from "./context";
import { Comments, Messages, People, Product, Run, SettingsTab, TeamActivity } from "./tabs";

const TABS = [["leads", "People", People], ["run", "Run", Run], ["comments", "Comments", Comments], ["messages", "Messages", Messages],
              ["setup", "Product", Product], ["settings", "Settings", SettingsTab], ["brain", "Team memory", TeamActivity]] as const;

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
  else if (n("ready")) { text = <><b>{n("ready")} approved message{n("ready") > 1 ? "s are" : " is"} ready.</b> Start the agent in “Send approved messages” to send them.</>; to = ["run", "Go to Run"]; }
  else if (!st.stats.leads) { text = <><b>Next:</b> press Start to find people to message.</>; to = ["run", "Go to Run"]; }
  else text = "All caught up. Press Start on the Run tab to find more people.";
  return (
    <Callout icon={<Sparkles className="size-4 shrink-0 text-acc" aria-hidden />}
      action={to && <Button size="sm" variant="primary" onClick={() => go(to![0])}>{to[1]} <ArrowRight aria-hidden /></Button>}>{text}</Callout>
  );
}

/** Open a drawer tab; "settings" also puts the cursor in the AI key field (that is what sends people there most often). */
export function useGoto(setTab: (t: string) => void) {
  return React.useCallback((t: string) => {
    setTab(t);
    if (t === "settings") setTimeout(() => document.getElementById("ai_key")?.focus(), 250);
  }, [setTab]);
}

function LastError() {
  const { st, goto } = usePg();
  if (!st?.last || st.last.ok || !st.last.error) return null;
  const m = friendly(st.last.error);
  if (isNoAiKey(m)) return <div className="mb-3"><Callout tone="warn" action={<Button size="sm" variant="primary" onClick={() => goto("settings")}>Add AI key</Button>}>{m}</Callout></div>;
  return <div className="mb-3 text-sm text-bad">{JOB_TITLE[st.last.kind] ?? "The last job"} didn&apos;t finish: {m}</div>;
}
const JOB_TITLE: Record<string, string> = { run: "The run", scan: "The page scan", replies: "Checking replies", doctor: "The Reddit check", send: "Sending", post: "Posting the comment", submit: "Posting", setup: "Reading your product", discover: "Finding subreddits" };

function Body({ id, onClose, tab, setTab }: { id: number; onClose: () => void; tab: string; setTab: (t: string) => void }) {
  const { profile, offline } = usePigeon(); const pg = usePg(); const p = profile(id);
  const Cur = TABS.find((t) => t[0] === tab)![2];
  const st = pg.st;
  return (
    <div id="pg" className="px-4 pb-10 pt-4 sm:px-7 sm:pt-5">
      <div className="mb-3.5 flex items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <Avatar name={p?.name ?? "?"} size={40} />
          <div>
            <h3 className="text-lg font-semibold tracking-tight">Agent · {p?.name ?? `account ${id}`}</h3>
            {st && (
              <div className="mt-0.5 flex flex-wrap items-center gap-x-2.5 text-xs text-mute">
                <span className="inline-flex items-center gap-1.5 font-medium text-ink"><Dot tone={st.state === "idle" ? "idle" : "ok"} /> {pigeonWords(st)}</span>
                <span>Looked at {st.stats.scanned} · found {people(st.stats.leads)} · sent {msgs(st.stats.dms)} · {st.daily.count}{st.cap != null ? ` of ${st.cap}` : ""} today{st.central ? " · team memory: on" : ""}</span>
              </div>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1">
          {st && st.state !== "idle" && <Button size="sm" variant="danger" onClick={() => pg.run(() => fetchStop(id), "Stopping the agent…")}>Stop</Button>}
          <IconButton icon={X} label="Close agent controls" onClick={onClose} />
        </div>
      </div>
      {st && !st.central && <div className="mb-3"><Callout tone="bad">Team memory is off: this account can&apos;t see who your other accounts already messaged, so it won&apos;t send anything.</Callout></div>}
      {st?.log?.length ? <div className="mb-3 truncate font-mono text-xs text-mute">{st.log[st.log.length - 1].msg}</div> : null}
      {offline && <div className="mb-3"><Callout tone="bad">Lost connection to the Pigeon app. Actions are paused until it&apos;s back.</Callout></div>}
      <LastError />
      <Next go={(t) => { setTab(t); pg.setMsg(""); }} />
      {/* phones: one select instead of seven tabs that scroll sideways */}
      <Select aria-label="Section" value={tab} onChange={(e) => setTab(e.target.value)} className="mb-4 mt-4 sm:hidden">
        {TABS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
      </Select>
      <div role="tablist" className="relative mb-5 mt-4 hidden gap-0.5 overflow-x-auto border-b border-line sm:flex">
        {TABS.map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => { setTab(k); pg.setMsg(""); }}
            className={`relative px-3 py-2.5 font-medium whitespace-nowrap transition-colors ${tab === k ? "text-ink" : "text-mute hover:text-ink"}`}>
            {label}
            {tab === k && <motion.span layoutId="pgtab" className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-ink" transition={{ type: "spring", stiffness: 500, damping: 40 }} />}
          </button>
        ))}
      </div>
      <AnimatePresence mode="wait" initial={false}>
        <motion.div key={tab} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.14 }}><fieldset disabled={!!offline} className="contents"><Cur /></fieldset></motion.div>
      </AnimatePresence>
    </div>
  );
}
const fetchStop = (id: number) => import("@/lib/api").then(({ api }) => api("POST", `/profiles/${id}/pigeon/stop`));

export function PigeonDrawer({ id, onClose }: { id: number | null; onClose: () => void }) {
  const [tab, setTab] = React.useState<string>("leads");
  const confirm = useConfirm();
  const dirty = React.useRef(false);
  const setDirty = React.useCallback((d: boolean) => { dirty.current = d; }, []);
  // Unsaved edits (Settings) survive nothing silently: leaving the tab or closing the drawer asks first.
  const leave = React.useCallback(async () => {
    if (!dirty.current) return true;
    const ok = await confirm({ title: "Discard unsaved changes?", body: "Your changes to this account's settings haven't been saved.", action: "Discard changes", cancel: "Keep editing", danger: true });
    if (ok) dirty.current = false;
    return ok;
  }, [confirm]);
  const open = useGoto(setTab);
  const goto = React.useCallback(async (t: string) => { if (t === tab || (await leave())) open(t); }, [leave, open, tab]);
  const close = React.useCallback(async () => { if (await leave()) onClose(); }, [leave, onClose]);
  React.useEffect(() => { if (id == null) setTab("leads"); }, [id]);
  return (
    <Drawer open={id != null} onOpenChange={(o) => { if (!o) close(); }} label="Agent controls">
      {id != null && <PgProvider id={id} goto={goto} setDirty={setDirty}><Body id={id} onClose={close} tab={tab} setTab={goto} /></PgProvider>}
    </Drawer>
  );
}
