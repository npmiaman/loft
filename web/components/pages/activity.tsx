"use client";
import * as React from "react";
import { api } from "@/lib/api";
import { usePigeon } from "@/lib/store";
import type { PigeonState } from "@/lib/types";
import { Callout, EmptyState, SkeletonRows } from "../ui/bits";
import { When } from "../ui/when";

export type AuditRow = { at: number; email: string | null; action: string; detail: string; profile_id?: number | null };
const ROLE: Record<string, string> = { owner: "owner", admin: "admin", member: "member" };
const LEVEL: Record<string, string> = { run: "can open", edit: "can edit" };

/** One audit entry as a sentence ("owner@x.io changed bea@x.io's role to admin"), with names instead of ids. */
export function describe(a: AuditRow, s: PigeonState): { who: string; what: string } {
  const who = a.email || "Someone";
  const user = (id: string) => s.members?.find((m) => String(m.id) === id)?.email ?? "a former member";
  const acct = a.profile_id ? s.profiles?.find((p) => p.id === a.profile_id)?.name ?? "a deleted account" : "an account";
  const d = a.detail || "";
  let m: RegExpMatchArray | null;
  const what = ((): string => {
    switch (a.action) {
      case "member.role": return (m = d.match(/user (\d+) -> (\w+)/)) ? `changed ${user(m[1])}'s role to ${ROLE[m[2]] ?? m[2]}` : "changed someone's role";
      case "member.removed": return (m = d.match(/user (\d+)/)) ? `removed ${user(m[1])} from the team` : "removed someone from the team";
      case "member.joined": return `joined the team as ${d ? `${/^[aeiou]/.test(d) ? "an" : "a"} ${ROLE[d] ?? d}` : "a member"}`;
      case "invite.created": return `created an invite for a new ${ROLE[d] ?? "member"}`;
      case "profile.created": return `created the account ${d || acct}`;
      case "profile.deleted": return `deleted the account ${d || acct}`;
      case "profile.started": return `opened ${acct}${d ? ` on ${d}` : ""}`;
      case "profile.stopped": return `closed ${acct}`;
      case "profile.force_unlocked": return `force-unlocked ${acct}`;
      case "profile.synced": return `saved ${acct}`;
      case "profile.shared": return (m = d.match(/user (\d+):(\w+)/)) ? `shared ${acct} with ${user(m[1])} (${LEVEL[m[2]] ?? m[2]})` : `shared ${acct}`;
      case "profile.unshared": return (m = d.match(/user (\d+)/)) ? `stopped sharing ${acct} with ${user(m[1])}` : `stopped sharing ${acct}`;
      case "runner.added": return `added the cloud runner ${d.split(" ")[0] || ""}`.trim();
      case "runner.removed": return `removed the cloud runner ${d}`.trim();
      case "template.created": return `created the template ${d}`.trim();
      case "template.deleted": return `deleted the template ${d}`.trim();
      case "team.created": return `created the team ${d}`.trim();
      case "team.key_rotation_started": return "started rotating the team key";
      case "team.key_rotation_aborted": return "stopped a team key rotation";
      case "team.key_rotated": return "rotated the team key";
      case "pigeon.config": return `published the team's agent setup${(m = d.match(/version (\d+)/)) ? ` (version ${m[1]})` : ""}`;
      default: return `${a.action.replace(/[._]/g, " ")}${d ? `: ${d}` : ""}`;
    }
  })();
  return { who, what };
}

/** The team's history, newest first, as sentences. Lives in the Overview's history panel. */
export function ActivityList() {
  const { s } = usePigeon();
  const [rows, setRows] = React.useState<AuditRow[] | null>(null); const [err, setErr] = React.useState("");
  React.useEffect(() => { api<AuditRow[]>("GET", "/audit").then(setRows).catch((e) => setErr(e.message)); }, []);
  if (err) return <Callout tone="bad">Couldn&apos;t load the activity: {err}</Callout>;
  if (!rows) return <SkeletonRows n={6} />;
  if (!rows.length) return <EmptyState compact title="Nothing has happened yet" />;
  return (
    <ol className="divide-y divide-line">
      {rows.map((a, i) => {
        const x = describe(a, s);
        return (
          <li key={i} className="flex gap-3 py-3 text-sm">
            <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-line2" aria-hidden />
            <div className="min-w-0"><div><b className="font-medium">{x.who}</b> {x.what}</div><div className="text-xs text-mute"><When t={a.at} /></div></div>
          </li>
        );
      })}
    </ol>
  );
}
