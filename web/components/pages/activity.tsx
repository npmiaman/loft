"use client";
import * as React from "react";
import { api } from "@/lib/api";
import { useLoft } from "@/lib/store";
import { Card, PageHead } from "../ui/bits";
import { Rows } from "./overview";

export function ActivityPage() {
  const { s, canAdmin } = useLoft();
  const [rows, setRows] = React.useState<any[] | null>(null);
  React.useEffect(() => { if (canAdmin) api<any[]>("GET", "/audit").then(setRows).catch(() => setRows([])); }, [canAdmin]);
  if (!canAdmin) return <p className="text-mute">Only owners and admins can see team activity.</p>;
  const name = (id: number) => s.profiles?.find((p) => p.id === id)?.name || (id ? `#${id}` : "");
  return (
    <>
      <PageHead title="Activity" sub="Everything that changed in this team." />
      <Card><Rows head={["When", "Who", "What", "Account", "Detail"]} none="Nothing yet."
        rows={(rows ?? []).map((a) => [new Date(a.at * 1000).toLocaleString(), a.email, a.action, name(a.profile_id), <span key="d" className="text-mute">{a.detail}</span>])} /></Card>
    </>
  );
}
