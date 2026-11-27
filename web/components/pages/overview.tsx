"use client";
import { animate, useMotionValue, useTransform, motion } from "motion/react";
import * as React from "react";
import { api } from "@/lib/api";
import { useLoft } from "@/lib/store";
import type { Overview as O } from "@/lib/types";
import { Callout, Card, PageHead } from "../ui/bits";

function Count({ value }: { value: number }) {
  const mv = useMotionValue(0); const text = useTransform(mv, (v) => Math.round(v).toString());
  React.useEffect(() => { const c = animate(mv, value, { duration: 0.6, ease: "easeOut" }); return () => c.stop(); }, [value, mv]);
  return <motion.span>{text}</motion.span>;
}
const Stat = ({ k, v, sub }: { k: string; v: React.ReactNode; sub?: string }) => (
  <Card className="p-4"><div className="text-[13px] text-mute">{k}</div><div className="mt-0.5 text-[28px] font-semibold tracking-tight">{v}</div>{sub && <div className="text-xs text-mute">{sub}</div>}</Card>
);

export function OverviewPage() {
  const { canAdmin } = useLoft();
  const [o, setO] = React.useState<O | null>(null); const [err, setErr] = React.useState("");
  React.useEffect(() => { if (canAdmin) api<O>("GET", "/overview").then(setO).catch((e) => setErr(e.message)); }, [canAdmin]);
  if (!canAdmin) return <p className="text-mute">Only owners and admins can see the overview.</p>;
  if (err) return <p className="text-bad">{err}</p>;
  if (!o) return null;
  return (
    <>
      <PageHead title="Overview" sub="How the team is doing right now." />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat k="Accounts" v={<Count value={o.profiles} />} />
        <Stat k="Members" v={<Count value={o.members} />} sub={o.members_without_key.length ? `${o.members_without_key.length} without a key` : "all have keys"} />
        <Stat k="Running now" v={<Count value={o.running.length} />} />
        <Stat k="Stored data" v={`${(o.bytes / 1048576).toFixed(1)} MB`} sub={`${o.versions} snapshots`} />
        <Stat k="Open invites" v={<Count value={o.open_invites} />} />
        <Stat k="Key epoch" v={<Count value={o.key_epoch} />} sub={o.rotating ? "rotating…" : o.last_rotation ? "rotated " + new Date(o.last_rotation * 1000).toLocaleDateString() : "never rotated"} />
      </div>
      {o.members_without_key.length > 0 && <div className="mt-4"><Callout tone="warn"><b>{o.members_without_key.length} member(s) have no encryption key.</b> <span className="text-mute">{o.members_without_key.join(", ")}</span></Callout></div>}
      <h3 className="mb-3 mt-8 text-base font-semibold">Running now</h3>
      <Card><Rows head={["Account", "Who", "Device"]} rows={o.running.map((r) => [r.name, r.email, r.device])} none="Nothing running." /></Card>
      <h3 className="mb-3 mt-8 text-base font-semibold">Recent activity</h3>
      <Card><Rows head={["When", "Who", "What", "Detail"]} rows={o.recent.map((a) => [new Date(a.at * 1000).toLocaleString(), a.email || "-", a.action, a.detail])} none="No activity yet." /></Card>
    </>
  );
}

export function Rows({ head, rows, none }: { head: string[]; rows: React.ReactNode[][]; none: string }) {
  return (
    <div className="overflow-x-auto"><table className="w-full text-[13.5px]">
      <thead><tr>{head.map((h) => <th key={h} className="px-4 py-3 text-left text-xs font-medium text-mute">{h}</th>)}</tr></thead>
      <tbody>{rows.map((r, i) => <tr key={i} className="border-t border-line hover:bg-hover/45">{r.map((c, j) => <td key={j} className="px-4 py-3">{c}</td>)}</tr>)}
        {!rows.length && <tr><td colSpan={head.length} className="px-4 py-6 text-mute">{none}</td></tr>}</tbody>
    </table></div>
  );
}
