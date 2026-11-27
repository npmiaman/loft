"use client";
import { ExternalLink } from "lucide-react";
import { usePigeon } from "@/lib/store";
import { Card, PageHead, Pill } from "../ui/bits";
import { Rows } from "./overview";

export function TeamPage() {
  const { s } = usePigeon();
  return (
    <>
      <PageHead title="Team" sub="People, roles and shared settings." />
      <Card><Rows head={["Member", "Role"]} none="No members."
        rows={(s.members ?? []).map((m) => [<span key="n">{m.name} <span className="text-xs text-mute">{m.email}</span></span>, <Pill key="r">{m.role}</Pill>])} /></Card>
      <Card className="mt-4 flex items-center justify-between gap-4 p-4">
        <p className="text-[13.5px] text-mute">Invites, roles, key rotation, backups, templates and cloud runners are still in the classic view while they move over.</p>
        <a href="/legacy/" className="inline-flex h-9 shrink-0 items-center gap-2 rounded-lg border border-line2 px-3.5 font-medium hover:bg-hover">Open classic view <ExternalLink className="size-3.5" /></a>
      </Card>
    </>
  );
}
