"use client";
import { Copy, ExternalLink, X } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { Lead } from "@/lib/types";
import { when } from "@/lib/utils";
import { Card, Pill } from "../ui/bits";
import { Button } from "../ui/button";
import { Field, Input, Select, Textarea } from "../ui/field";
import { useData, usePg } from "./context";

export const LEAD_WORDS: Record<string, string> = { queued: "waiting for a message", draft: "needs your approval", ready: "approved", sent: "sent", replied: "replied", failed: "failed", skipped: "skipped" };
export const SETTING_LABELS: Record<string, string> = {
  model: "AI model", my_username: "This account's Reddit username", daily_dm_cap: "Max messages per day", warmup: "Start slowly (warm-up)",
  active_start: "Only work from this hour (0-23)", active_end: "Only work until this hour (0-23)", min_gap_sec: "Shortest wait between messages (seconds)",
  max_gap_sec: "Longest wait between messages (seconds)", pace: "Speed (1 = normal, lower = slower)", min_fit: "Minimum match score (0-100)",
  session_minutes: "How long each run lasts (minutes)", upvotes: "Upvote posts while browsing", max_upvotes_per_session: "Max upvotes per run",
  dm_mode: "How messages are written (ai or template)", approve_ai_drafts: "I approve AI-written messages first", ai_guidance: "Extra instructions for the AI",
  use_team_setup: "Use the team's shared setup", auto_check_replies: "Check for replies automatically", posts_per_day: "Max posts per day",
};
const Section = ({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) => (
  <Card className="mb-3 p-4"><div className="mb-3"><div className="font-semibold">{title}</div>{hint && <div className="mt-0.5 text-[13px] text-mute">{hint}</div>}</div>{children}</Card>
);
const empty = (t: string) => <p className="py-8 text-center text-[13.5px] text-mute">{t}</p>;
const download = (name: string, text: string, type: string) => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([text], { type })); a.download = name; a.click(); };

/* ---------------------------------------------------------------- People */
export function People() {
  const pg = usePg();
  const L = useData(async (p) => (await p.op("leads_list")).leads as Lead[]) ?? [];
  const [filter, setFilter] = React.useState("all"); const [edits, setEdits] = React.useState<Record<string, string>>({}); const [paste, setPaste] = React.useState("");
  const shown = L.filter((l) => filter === "all" || l.status === filter).slice(0, 100);
  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Select value={filter} onChange={(e) => setFilter(e.target.value)} className="w-52" aria-label="Filter people">
          <option value="all">everyone</option>{Object.entries(LEAD_WORDS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </Select>
        <Button size="sm" onClick={() => pg.run(() => pg.op("lead_write", { all: true }))}>Write all queued</Button>
        <Button size="sm" onClick={() => pg.run(() => pg.op("lead_approve", { all: true }))}>Approve all drafts</Button>
        <Button size="sm" onClick={async () => download("pigeon-leads.csv", (await pg.op("leads_export")).csv, "text/csv")}>Export CSV</Button>
        <Button size="sm" variant="danger" onClick={() => confirm("Delete ALL leads?") && pg.run(() => pg.op("leads_clear"))}>Clear all</Button>
      </div>
      {shown.map((l) => {
        const sent = l.status === "sent" || l.status === "replied", u = l.username, text = edits[u] ?? l.message ?? "";
        return (
          <Card key={u} className="mb-2.5 p-3.5">
            <div className="flex flex-wrap items-center gap-2">
              <a href={`https://www.reddit.com/user/${u}`} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-semibold">u/{u}<ExternalLink className="size-3 text-mute" /></a>
              <Pill>{l.fit}</Pill><Pill tone={sent ? "ok" : l.status === "failed" ? "bad" : l.status === "draft" ? "warn" : undefined}>{LEAD_WORDS[l.status] || l.status}</Pill>
              {l.subreddit && <span className="text-xs text-mute">r/{l.subreddit}</span>}
            </div>
            <p className="mt-1 text-[13px] text-mute">{l.reason}{l.error && <span className="text-bad"> · {l.error}</span>}</p>
            {(l.message || edits[u] != null) && <Textarea className="mt-2 min-h-[72px]" disabled={sent} value={text} onChange={(e) => setEdits({ ...edits, [u]: e.target.value })} />}
            <div className="mt-2.5 flex flex-wrap gap-2">
              {edits[u] != null && <Button size="sm" variant="primary" onClick={async () => { const m = edits[u]; setEdits(({ [u]: _, ...r }) => r); await pg.run(() => pg.op("lead_edit", { username: u, message: m }), "saved"); }}>Save</Button>}
              {(l.status === "queued" || l.status === "failed") && <Button size="sm" onClick={() => pg.run(() => pg.op("lead_write", { username: u }))}>Write message</Button>}
              {l.status === "draft" && <Button size="sm" variant="primary" onClick={() => pg.run(() => pg.op("lead_approve", { username: u }))}>Approve</Button>}
              {(l.status === "draft" || l.status === "ready") && <Button size="sm" variant="primary" disabled={pg.busy} onClick={() => pg.run(() => pg.job("send", { username: u }), "sending…")}>Send now</Button>}
              {l.status === "sent" && <Button size="sm" onClick={() => pg.run(() => pg.op("lead_replied", { username: u }))}>Mark replied</Button>}
              {!sent && l.status !== "skipped" && <Button size="sm" onClick={() => pg.run(() => pg.op("lead_skip", { username: u }))}>Skip</Button>}
            </div>
          </Card>
        );
      })}
      {!shown.length && empty("No one here yet. Press Start on the Run tab to find people, or add usernames below.")}
      <h4 className="mb-1 mt-6 text-[13px] font-semibold">Add people by username</h4>
      <p className="mb-2 text-[13px] text-mute">One username per line. People your team already contacted are skipped.</p>
      <Textarea id="pgimp" value={paste} onChange={(e) => setPaste(e.target.value)} />
      <Button className="mt-2" onClick={async () => { await pg.run(() => pg.op("leads_import", { text: paste })); setPaste(""); }}>Import to queue</Button>
    </div>
  );
}

/* ---------------------------------------------------------------- Run */
export function Run() {
  const pg = usePg(); const [mode, setMode] = React.useState("find"); const [minutes, setMinutes] = React.useState(""); const [noScore, setNoScore] = React.useState(false);
  const doctor = pg.st?.last?.kind === "doctor" ? pg.st.last.out : null;
  return (
    <div>
      <Section title="Start the agent" hint="It works in its own tab. Your tabs are left alone. Messages and comments go through the daily cap and the team's shared history.">
        <div className="flex flex-wrap items-center gap-2">
          <Select id="pgmode" value={mode} onChange={(e) => setMode(e.target.value)} className="w-64">
            <option value="find">Find people (sends nothing)</option><option value="dm">Send approved messages</option><option value="full">Find people + message them</option>
          </Select>
          <Input type="number" value={minutes} onChange={(e) => setMinutes(e.target.value)} placeholder="how many minutes (optional)" className="w-56" />
          <Button variant="primary" disabled={pg.busy} onClick={() => pg.run(() => pg.job("run", { mode, minutes: parseFloat(minutes) || null }), "started")}>▶ Start</Button>
        </div>
      </Section>
      <Section title="Add the people on this page" hint="Adds everyone on the Reddit page you're looking at to the list.">
        <label className="mb-2 flex items-center gap-2 text-[13px]"><input type="checkbox" checked={noScore} onChange={(e) => setNoScore(e.target.checked)} /> skip AI scoring</label>
        <Button disabled={pg.busy} onClick={() => pg.run(() => pg.job("scan", { no_score: noScore }), "scanning…")}>Queue everyone on this page</Button>
      </Section>
      <Section title="Replies" hint="Look for answers to messages already sent.">
        <Button disabled={pg.busy} onClick={() => pg.run(() => pg.job("replies"), "checking…")}>Check replies</Button>
      </Section>
      <Section title="Check that Reddit works with the agent" hint="Safe: it only looks, it sends nothing. Run it once on a new account.">
        <Button disabled={pg.busy} onClick={() => pg.run(() => pg.job("doctor"), "checking Reddit… (the report appears here)")}>Run the check</Button>
        {doctor && <pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-hover/60 p-3 font-mono text-xs">{doctor}</pre>}
      </Section>
    </div>
  );
}

/* ---------------------------------------------------------------- Comments */
export function Comments() {
  const pg = usePg();
  const d = useData(async (p) => ({ O: (await p.op("opps_list")).opportunities as any[], I: (await p.op("ideas_list")).ideas as any[], P: (await p.op("posts_list")).posted as any[] }));
  const { O, I, P } = d ?? { O: [], I: [], P: [] };
  return (
    <div>
      <h4 className="mb-2 text-[13px] font-semibold">Comment opportunities</h4>
      {O.map((o) => (
        <Card key={o.id} className="mb-2.5 p-3.5">
          <div className="flex flex-wrap items-center gap-2"><b>r/{o.subreddit}</b><span>{o.title}</span><Pill>{o.status}</Pill>
            <a className="inline-flex items-center gap-1 text-xs" href={o.post_url} target="_blank" rel="noreferrer">thread <ExternalLink className="size-3" /></a></div>
          <p className="mt-1 text-[13px] text-mute">{o.why}</p>
          <pre className="mt-2 whitespace-pre-wrap rounded-lg border border-line bg-hover/60 p-3 font-mono text-xs">{o.draft}</pre>
          {o.status === "pending" && <div className="mt-2.5 flex gap-2">
            <Button size="sm" variant="primary" disabled={pg.busy} onClick={() => pg.run(() => pg.job("post", { id: o.id }), "posting…")}>Approve &amp; post</Button>
            <Button size="sm" onClick={() => pg.run(() => pg.op("opp_skip", { id: o.id }))}>Skip</Button></div>}
        </Card>
      ))}
      {!O.length && empty("None yet. They're drafted while the agent looks through your subreddits.")}
      <h4 className="mb-2 mt-6 text-[13px] font-semibold">Post ideas</h4>
      <Button size="sm" onClick={() => pg.run(() => pg.op("ideas_generate"), "writing ideas…")}>Generate 5 post ideas</Button>
      {I.map((i) => (
        <Card key={i.id} className="my-2.5 p-3.5">
          <div><b>r/{i.subreddit}</b> — {i.title}</div>
          <pre className="mt-2 whitespace-pre-wrap rounded-lg border border-line bg-hover/60 p-3 font-mono text-xs">{i.body}</pre>
          <p className="mt-1 text-xs text-mute">{i.angle}</p>
          <div className="mt-2.5 flex flex-wrap gap-2">
            <Button size="sm" onClick={() => { navigator.clipboard.writeText(`${i.title}\n\n${i.body}`); toast.success("Copied"); }}><Copy className="size-3.5" /> Copy</Button>
            <a className="inline-flex h-8 items-center rounded-lg border border-line2 px-3 text-[13px] font-medium hover:bg-hover" href={`https://www.reddit.com/r/${i.subreddit}/submit`} target="_blank" rel="noreferrer">Open submit page</a>
            <Button size="sm" variant="primary" disabled={pg.busy} onClick={() => confirm(`Submit this to r/${i.subreddit} now, from this account?`) && pg.run(() => pg.job("submit", { id: i.id }), "posting…")}>Submit now</Button>
            <Button size="sm" onClick={() => pg.run(() => pg.op("idea_posted", { id: i.id }))}>I posted it myself</Button>
          </div>
        </Card>
      ))}
      {P.length > 0 && <><h4 className="mb-2 mt-6 text-[13px] font-semibold">Posted by the agent</h4>
        {P.map((x) => <div key={x.id} className="text-[13px]"><b>r/{x.subreddit}</b> {x.title} <a href={x.url} target="_blank" rel="noreferrer">thread ↗</a></div>)}</>}
    </div>
  );
}

/* ---------------------------------------------------------------- Messages */
export function Messages() {
  const pg = usePg();
  const d = useData(async (p) => ({ tpl: await p.op("template_show"), s: (await p.op("settings_set")).settings as Record<string, any> }));
  const [body, setBody] = React.useState<string | null>(null); const [intensity, setIntensity] = React.useState("medium");
  const s = d?.s ?? {}, tpl = d?.tpl ?? {};
  const text = body ?? tpl.template ?? "";
  const setting = (pairs: Record<string, unknown>) => pg.run(() => pg.op("settings_set", { pairs }), "");
  return (
    <div>
      <Section title="Who writes the messages" hint="AI drafts wait for your approval unless you turn that off.">
        <div className="flex flex-wrap items-center gap-2">
          <Button variant={s.dm_mode === "ai" ? "primary" : "secondary"} onClick={() => setting({ dm_mode: "ai" })}>AI writes each message</Button>
          <Button variant={s.dm_mode === "template" ? "primary" : "secondary"} onClick={() => setting({ dm_mode: "template" })}>Use my template</Button>
          <label className="ml-2 flex items-center gap-2 text-[13px]"><input type="checkbox" checked={!!s.approve_ai_drafts} onChange={(e) => setting({ approve_ai_drafts: e.target.checked })} /> AI drafts wait for my approval</label>
        </div>
      </Section>
      <Section title="Template" hint="{Hey|Hi|Hello} {name}, … — variables: {name} {subreddit} {topic}">
        <Textarea id="pgtpl" className="min-h-28" value={text} onChange={(e) => setBody(e.target.value)} />
        <div className="mt-2.5 flex flex-wrap items-center gap-2">
          <Button variant="primary" onClick={async () => { await pg.run(() => pg.op("template_set", { body: text }), "saved"); setBody(null); }}>Save</Button>
          <Select value={intensity} onChange={(e) => setIntensity(e.target.value)} className="w-28"><option>light</option><option>medium</option><option>heavy</option></Select>
          <Button onClick={() => pg.run(() => pg.op("template_spin", { intensity }).then((r) => ({ out: "✨ spun. " + r.out })))}>✨ Add spintax with AI</Button>
          <Button onClick={() => pg.run(() => pg.op("template_preview", { n: 4 }))}>Preview variations</Button>
          {tpl.undo && <Button onClick={() => pg.run(() => pg.op("template_undo"), "restored")}>Undo AI spin</Button>}
        </div>
      </Section>
    </div>
  );
}

/* ---------------------------------------------------------------- Product */
export function Product() {
  const pg = usePg();
  const d = useData(async (p) => ({ product: (await p.op("product_show")).product, subs: (await p.op("subs_edit")).subreddits as any[] }));
  const product = d?.product, subs = d?.subs ?? [];
  const [url, setUrl] = React.useState<string | null>(null); const [add, setAdd] = React.useState("");
  return (
    <div>
      <Section title="What are you promoting?" hint="The agent reads your site to learn who to look for.">
        <div className="flex gap-2">
          <Input value={url ?? product?.url ?? ""} onChange={(e) => setUrl(e.target.value)} placeholder="https://your-product.com" />
          <Button variant="primary" disabled={pg.busy} onClick={() => pg.run(() => pg.job("setup", { url: url ?? product?.url ?? "" }), "analyzing + finding subreddits…")}>{product ? "Re-analyze" : "Analyze my product"}</Button>
        </div>
        {product && (
          <div className="mt-3 rounded-lg border border-line bg-hover/50 p-3 text-[13px]">
            <b>{product.name}</b> — {product.summary}<br /><span className="text-mute">ICP:</span> {product.icp}<br /><span className="text-mute">Offer:</span> {product.offer}
          </div>
        )}
      </Section>
      <Section title="Subreddits">
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <Button size="sm" disabled={pg.busy || !product} onClick={() => pg.run(() => pg.job("discover"), "finding subreddits…")}>Re-run discovery</Button>
          <Input value={add} onChange={(e) => setAdd(e.target.value)} placeholder="add r/name" className="w-44" />
          <Button size="sm" onClick={async () => { await pg.run(() => pg.op("subs_edit", { add: [add] }), ""); setAdd(""); }}>Add</Button>
        </div>
        <div className="divide-y divide-line rounded-lg border border-line">
          {subs.map((x) => (
            <div key={x.name} className="flex items-center gap-3 px-3 py-2 text-[13px]">
              <input type="checkbox" checked={!!x.enabled} onChange={(e) => pg.run(() => pg.op("subs_edit", { [e.target.checked ? "enable" : "disable"]: [x.name] }), "")} />
              <b className="w-40 truncate">r/{x.name}</b><span className="text-mute">score {x.score}</span><span className="text-mute">rules: {x.rules_risk}</span>
              <span className="min-w-0 flex-1 truncate text-mute">{x.reason}</span>
              <button aria-label={`Remove r/${x.name}`} className="text-mute hover:text-bad" onClick={() => pg.run(() => pg.op("subs_edit", { remove: [x.name] }), "")}><X className="size-4" /></button>
            </div>
          ))}
          {!subs.length && <p className="px-3 py-4 text-[13px] text-mute">No subreddits yet.</p>}
        </div>
      </Section>
    </div>
  );
}

/* ---------------------------------------------------------------- Settings */
export function SettingsTab() {
  const pg = usePg();
  const s = useData(async (p) => (await p.op("settings_set")).settings as Record<string, any>);
  const [draft, setDraft] = React.useState<Record<string, any>>({});
  if (!s) return empty("Loading…");
  const v = (k: string) => (k in draft ? draft[k] : s[k]);
  return (
    <div>
      <div className="grid max-w-2xl grid-cols-[minmax(0,260px)_1fr] items-center gap-x-4 gap-y-3">
        {Object.entries(s).filter(([k]) => k !== "warmup_start").map(([k, orig]) => (
          <React.Fragment key={k}>
            <span className="text-[13px] text-ink2">{SETTING_LABELS[k] || k}</span>
            {typeof orig === "boolean"
              ? <input type="checkbox" data-k={k} className="size-4" checked={!!v(k)} onChange={(e) => setDraft({ ...draft, [k]: e.target.checked })} />
              : <Input data-k={k} value={v(k) ?? ""} onChange={(e) => setDraft({ ...draft, [k]: typeof orig === "number" ? e.target.value : e.target.value })} />}
          </React.Fragment>
        ))}
      </div>
      <div className="mt-5 flex flex-wrap gap-2">
        <Button variant="primary" onClick={async () => {
          const pairs = Object.fromEntries(Object.entries(draft).map(([k, x]) => [k, typeof s[k] === "number" && x !== "" ? Number(x) : x]));
          await pg.run(() => pg.op("settings_set", { pairs }), "saved"); setDraft({});
        }}>Save settings</Button>
        <Button onClick={() => pg.run(() => pg.op("settings_set", { restart_warmup: true }), "warm-up restarted from today")}>Restart warm-up from today</Button>
      </div>
      <p className="mt-3 text-xs text-mute">The DeepSeek key stays on this machine: <code className="rounded bg-hover px-1">pigeon agent config --deepseek-key KEY</code></p>
    </div>
  );
}

/* ---------------------------------------------------------------- Team activity */
export function TeamActivity() {
  const pg = usePg();
  const b = useData(async () => await api<any>("GET", "/pigeon/brain"));
  if (!b) return empty("Loading…");
  const k = b.summary.by_kind || {};
  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-2">
        <Button size="sm" onClick={() => pg.run(() => pg.op("brain_backfill"))}>Record this account's past DMs &amp; comments</Button>
        <Button size="sm" onClick={() => pg.run(() => pg.op("team_pull", { force: true }))}>Pull team setup</Button>
        <Button size="sm" onClick={() => confirm("Share the product, subreddits, template and shared settings of this account with the whole team?") && pg.run(() => pg.op("team_publish"))}>Publish this account&apos;s setup to the team</Button>
      </div>
      <p className="mb-3 text-[13px] text-mute">Team setup v{b.team_setup.version}{b.team_setup.product ? ` · ${b.team_setup.product}` : ""} · {b.team_setup.subreddits} subreddits · limits: {Object.entries(b.team_setup.limits || {}).map(([a, x]) => `${a}=${x}`).join(", ") || "none"}</p>
      <p className="mb-3 text-[13px]"><b>Our accounts:</b> {b.accounts.map((a: any) => `u/${a.username} (${a.profile || "#" + a.profile_id})`).join(" · ") || <span className="text-mute">none registered yet</span>}</p>
      <Section title={`Team memory · last ${b.summary.days} days, every account`} hint={`${k.dm || 0} DMs · ${k.reply || 0} replies · ${k.comment || 0} comments · ${k.post || 0} posts · ${k.upvote || 0} upvotes${b.summary.stuck_claims ? ` · ${b.summary.stuck_claims} stuck claim(s)` : ""}`}>
        <Table head={["Subreddit", "DMs", "Replies", "Comments", "Posts", "Upvotes", "Last"]} rows={b.summary.by_subreddit.map((r: any) => [`r/${r.subreddit}`, r.dms, r.replies, r.comments, r.posts, r.upvotes, r.last_at ? when(r.last_at) : ""])} />
        <div className="h-3" />
        <Table head={["Account", "DMs", "Replies", "Comments", "Posts", "Upvotes"]} rows={b.summary.by_profile.map((r: any) => [r.profile || "-", r.dms, r.replies, r.comments, r.posts, r.upvotes])} />
      </Section>
      <h4 className="mb-2 mt-5 text-[13px] font-semibold">Recent</h4>
      <Table head={["When", "What", "Status", "Account", "Where", "Who"]} rows={b.events.map((e: any) => [when(e.at), e.kind, e.status, e.profile || "-", e.subreddit ? `r/${e.subreddit}` : "", e.target ? `u/${e.target}` : ""])} />
    </div>
  );
}
function Table({ head, rows }: { head: string[]; rows: (string | number)[][] }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-line"><table className="w-full text-[13px]">
      <thead><tr>{head.map((h) => <th key={h} className="px-3 py-2 text-left text-xs font-medium text-mute">{h}</th>)}</tr></thead>
      <tbody>{rows.map((r, i) => <tr key={i} className="border-t border-line">{r.map((c, j) => <td key={j} className="px-3 py-2">{c}</td>)}</tr>)}
        {!rows.length && <tr><td colSpan={head.length} className="px-3 py-4 text-mute">Nothing yet.</td></tr>}</tbody>
    </table></div>
  );
}
