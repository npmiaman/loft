"use client";
import { animate, useMotionValue, useTransform, motion } from "motion/react";
import * as React from "react";
import { api } from "@/lib/api";
import { usePigeon } from "@/lib/store";
import type { Overview as O } from "@/lib/types";
import { Callout, Card, EmptyState, PageHead, Skeleton } from "../ui/bits";
import { IconButton } from "../ui/button";
import { Drawer } from "../ui/dialog";
import { ActivityList } from "./activity";
import { History, X } from "lucide-react";

function Count({ value }: { value: number }) {
  const mv = useMotionValue(0); const text = useTransform(mv, (v) => Math.round(v).toString());
  React.useEffect(() => { const c = animate(mv, value, { duration: 0.6, ease: "easeOut" }); return () => c.stop(); }, [value, mv]);
  return <motion.span>{text}</motion.span>;
}
const Stat = ({ k, v, sub }: { k: string; v: React.ReactNode; sub?: string }) => (
  <Card className="p-4"><div className="text-sm text-mute">{k}</div><div className="mt-0.5 text-display font-semibold tracking-tight tabular-nums">{v}</div>{sub && <div className="text-xs text-mute">{sub}</div>}</Card>
);

type Brain = { summary: { by_kind: Record<string, number> } };

export function OverviewPage() {
  const { s, canAdmin, onPoll } = usePigeon();
  const [o, setO] = React.useState<O | null>(null); const [err, setErr] = React.useState("");
  const [today, setToday] = React.useState<Record<string, number> | null>(null);
  const [history, setHistory] = React.useState(false);
  React.useEffect(() => { // old #activity links open the history
    if (window.location.hash === "#activity") { setHistory(true); window.history.replaceState(null, "", "#overview"); }
  }, []);
  // Loads now and again after each poll of the app's state, so the numbers stay current while the page is open.
  React.useEffect(() => {
    if (!canAdmin) return;
    let live = true;
    const load = () => {
      api<O>("GET", "/overview").then((x) => { if (live) { setO(x); setErr(""); } }).catch((e) => live && setErr(e.message));
      api<Brain>("GET", "/pigeon/brain?days=1&limit=1").then((b) => live && setToday(b.summary.by_kind || {})).catch(() => live && setToday(null));
    };
    load();
    let n = 0;
    const off = onPoll(() => { if (++n % 2 === 0) load(); });  // every other poll: about every 6 s
    return () => { live = false; off(); };
  }, [canAdmin, onPoll]);
  if (!canAdmin) return <EmptyState title="Only owners and admins can see the overview" />;
  return (
    <>
      <PageHead title="Overview" sub="How the team is doing right now."
        actions={<IconButton icon={History} label="Activity history" onClick={() => setHistory(true)} />} />
      <Drawer open={history} onOpenChange={setHistory} label="Activity history">
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-line bg-card px-6 py-4">
          <div><h2 className="text-lg font-semibold tracking-tight">Activity history</h2><p className="text-sm text-mute">Everything that changed in this team, newest first.</p></div>
          <IconButton icon={X} label="Close history" onClick={() => setHistory(false)} />
        </div>
        <div className="px-6 py-2"><ActivityList /></div>
      </Drawer>
      {today && (
        <div className="mb-6">
          <h2 className="mb-3 text-lg font-semibold">Today, across the team</h2>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat k="Messages sent today" v={<Count value={today.dm ?? 0} />} />
            <Stat k="Replies today" v={<Count value={today.reply ?? 0} />} />
            <Stat k="People found today" v={<Count value={today.lead ?? 0} />} />
            <Stat k="Comments posted today" v={<Count value={today.comment ?? 0} />} />
          </div>
        </div>
      )}
      {err && !o ? <Callout tone="bad">Couldn&apos;t load the overview: {err}</Callout> : !o ? (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">{Array.from({ length: 6 }, (_, i) => <Card key={i} className="p-4"><Skeleton className="h-4 w-20" /><Skeleton className="mt-2 h-8 w-12" /></Card>)}</div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            <Stat k="Accounts" v={<Count value={o.profiles} />} />
            <Stat k="Members" v={<Count value={o.members} />} sub={o.members_without_key.length ? `${o.members_without_key.length} without a key` : "All have keys"} />
            <Stat k="Running now" v={<Count value={o.running.length} />} />
            <Stat k="Stored data" v={`${(o.bytes / 1048576).toFixed(1)} MB`} sub={`${o.versions} saved ${o.versions === 1 ? "version" : "versions"}`} />
            <Stat k="Unused invites" v={<Count value={o.open_invites} />} />
            <Stat k="Encryption key" v={o.rotating ? "Rotating" : o.last_rotation ? "Rotated" : "Original"} sub={o.rotating ? "A key rotation is in progress" : o.last_rotation ? `Last rotated ${new Date(o.last_rotation * 1000).toLocaleDateString()}` : "Never rotated"} />
          </div>
          {o.members_without_key.length > 0 && <div className="mt-4"><Callout tone="warn"><b>{o.members_without_key.length === 1 ? "1 member has" : `${o.members_without_key.length} members have`} no encryption key.</b> <span className="text-mute">{o.members_without_key.join(", ")}. Send them a new invite code from Settings → Members.</span></Callout></div>}
        </>
      )}
    </>
  );
}
