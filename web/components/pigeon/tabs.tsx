"use client";
import { Copy, ExternalLink, Play, Sparkles, X } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { Lead } from "@/lib/types";
import { friendly } from "@/lib/api";
import { modePlan, msgs, people } from "@/lib/utils";
import { useConfirm } from "../ui/confirm";
import { Card, EmptyState, Pill, SkeletonRows } from "../ui/bits";
import { Button, IconButton, buttonStyles } from "../ui/button";
import { Checkbox, Switch } from "../ui/checkbox";
import { DataTable } from "../ui/table";
import { When } from "../ui/when";
import { Field, Input, Select, Textarea } from "../ui/field";
import { useData, usePg } from "./context";

export const MODES = [["find", "Find people (sends nothing)"], ["full", "Find people and message them"], ["dm", "Send approved messages"]] as const;
export const LEAD_WORDS: Record<string, string> = { queued: "waiting for a message", draft: "needs your approval", ready: "approved", sent: "sent", replied: "replied", failed: "failed", skipped: "skipped" };
const Section = ({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) => (
  <Card className="mb-3 p-4"><div className="mb-3"><div className="font-semibold">{title}</div>{hint && <div className="mt-0.5 text-sm text-mute">{hint}</div>}</div>{children}</Card>
);
const empty = (t: string, body?: string) => <EmptyState compact title={t} body={body} />;
const download = (name: string, text: string, type: string) => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([text], { type })); a.download = name; a.click(); };

/* ---------------------------------------------------------------- People */
const PAGE = 100;
export function People() {
  const pg = usePg(); const confirm = useConfirm();
  const L = useData(async (p) => (await p.op("leads_list")).leads as Lead[]);
  const all = L ?? [];
  const [filter, setFilter] = React.useState("all"); const [edits, setEdits] = React.useState<Record<string, string>>({}); const [paste, setPaste] = React.useState("");
  const [q, setQ] = React.useState(""); const [sort, setSort] = React.useState<"fit" | "newest">("fit"); const [limit, setLimit] = React.useState(PAGE);
  // what each card is doing right now ("Approving…"), so a click shows on the card it was made on
  const [doing, setDoing] = React.useState<Record<string, string>>({});
  const needle = q.trim().toLowerCase().replace(/^u\//, "");
  const matched = all.filter((l) => (filter === "all" || l.status === filter)
    && (!needle || `${l.username} ${l.subreddit ?? ""} ${l.reason} ${l.message ?? ""}`.toLowerCase().includes(needle)));
  const sorted = sort === "fit" ? matched : [...matched].sort((a, b) => ((b as any).created_at ?? 0) - ((a as any).created_at ?? 0));
  const shown = sorted.slice(0, limit);
  const drafts = pg.st?.leads?.draft ?? all.filter((l) => l.status === "draft").length;
  const unsent = all.filter((l) => l.status !== "sent" && l.status !== "replied").length;
  const drop = (u: string) => setEdits(({ [u]: _, ...r }) => r);
  const on = async (u: string, label: string, fn: () => Promise<unknown>, ok?: string) => {
    setDoing((d) => ({ ...d, [u]: label }));
    try { return await pg.run(fn, ok); } finally { setDoing(({ [u]: _, ...r }) => r); }
  };
  // Approve and Send always use what is on screen: an unsaved edit is approved (and so saved) with it.
  const approve = async (u: string) => { const m = edits[u]; if (await on(u, "Approving…", () => pg.op("lead_approve", m != null ? { username: u, message: m } : { username: u }), `Approved the message to u/${u}`)) drop(u); };
  const sendNow = async (l: Lead) => {
    const u = l.username, m = edits[u];
    if (!(await confirm({ title: `Send this message to u/${u}?`, body: "It goes out now, from this account, and counts toward today's limit. It can't be unsent.", action: "Send message" }))) return;
    await on(u, "Sending…", async () => {
      if (m != null || l.status === "draft") await pg.op("lead_approve", m != null ? { username: u, message: m } : { username: u });
      drop(u);
      return pg.job("send", { username: u });
    }, `Sending to u/${u}. The result shows here when it's done.`);
  };
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Input density="sm" type="search" aria-label="Search people" placeholder="Search people" value={q} onChange={(e) => { setQ(e.target.value); setLimit(PAGE); }} className="w-full sm:w-52" />
        <Select density="sm" value={filter} onChange={(e) => { setFilter(e.target.value); setLimit(PAGE); }} className="w-44" aria-label="Filter people">
          <option value="all">Everyone</option>{Object.entries(LEAD_WORDS).map(([k, v]) => <option key={k} value={k}>{v[0].toUpperCase() + v.slice(1)}</option>)}
        </Select>
        <Select density="sm" value={sort} onChange={(e) => setSort(e.target.value as "fit" | "newest")} className="w-36" aria-label="Sort">
          <option value="fit">Best match</option><option value="newest">Newest</option>
        </Select>
      </div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={() => pg.run(() => pg.op("lead_write", { all: true }))}>Write all queued</Button>
        <Button size="sm" disabled={!drafts} onClick={async () => {
          if (await confirm({ title: `Approve ${msgs(drafts)}?`, body: "Approved messages are sent when the agent runs in a sending mode, up to the daily limit.", action: `Approve ${drafts}` }))
            pg.run(() => pg.op("lead_approve", { all: true }));
        }}>Approve all ({drafts})</Button>
        <Button size="sm" onClick={async () => download("pigeon-leads.csv", (await pg.op("leads_export")).csv, "text/csv")}>Export CSV</Button>
        <Button size="sm" variant="danger" disabled={!unsent} onClick={async () => {
          if (await confirm({ title: `Clear ${people(unsent)} not yet messaged?`, body: "People already messaged stay in the list, and the agent will never message them again.", action: "Clear unsent leads", danger: true }))
            pg.run(() => pg.op("leads_clear", { unsent: true }));
        }}>Clear unsent leads</Button>
      </div>
      {L && all.length > 0 && <p className="mb-2 text-xs text-mute">Showing {shown.length} of {matched.length}{matched.length !== all.length ? ` (${all.length} in total)` : ""}</p>}
      {!L && <SkeletonRows n={4} />}
      {shown.map((l) => {
        const sent = l.status === "sent" || l.status === "replied", u = l.username, text = edits[u] ?? l.message ?? "", busy = doing[u];
        return (
          <Card key={u} data-lead={u} aria-busy={!!busy || undefined} className="mb-2.5 p-3.5">
            <div className="flex flex-wrap items-center gap-2">
              <a href={`https://www.reddit.com/user/${u}`} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-semibold">u/{u}<ExternalLink className="size-3 text-mute" aria-hidden /></a>
              <Pill>Match {l.fit}</Pill><Pill tone={sent ? "ok" : l.status === "failed" ? "bad" : l.status === "draft" ? "warn" : undefined}>{LEAD_WORDS[l.status] || l.status}</Pill>
              {l.subreddit && <span className="text-xs text-mute">r/{l.subreddit}</span>}
            </div>
            <p className="mt-1 text-sm text-mute">{l.reason}{l.error && <span className="text-bad"> · {friendly(l.error)}</span>}</p>
            {(l.message || edits[u] != null) && <Textarea aria-label={`Message to u/${u}`} className="mt-2 min-h-[72px]" disabled={sent || !!busy} value={text} onChange={(e) => setEdits({ ...edits, [u]: e.target.value })} />}
            <div className="mt-2.5 flex flex-wrap items-center gap-2">
              {busy ? <Button size="sm" loading>{busy}</Button> : <>
                {edits[u] != null && <Button size="sm" onClick={async () => { const m = edits[u]; if (await on(u, "Saving…", () => pg.op("lead_edit", { username: u, message: m }), "Saved")) drop(u); }}>Save</Button>}
                {(l.status === "queued" || l.status === "failed") && <Button size="sm" onClick={() => on(u, "Writing…", () => pg.op("lead_write", { username: u }))}>Write message</Button>}
                {l.status === "draft" && <Button size="sm" variant="primary" onClick={() => approve(u)}>Approve</Button>}
                {(l.status === "draft" || l.status === "ready") && <Button size="sm" disabled={pg.busy} onClick={() => sendNow(l)}>Send now</Button>}
                {l.status === "sent" && <Button size="sm" onClick={() => on(u, "Saving…", () => pg.op("lead_replied", { username: u }), `Marked u/${u} as replied`)}>Mark replied</Button>}
                {!sent && l.status !== "skipped" && <Button size="sm" onClick={() => on(u, "Skipping…", () => pg.op("lead_skip", { username: u }), `Skipped u/${u}`)}>Skip</Button>}
              </>}
            </div>
          </Card>
        );
      })}
      {matched.length > shown.length && <Button size="sm" className="mb-2" onClick={() => setLimit(limit + PAGE)}>Show more</Button>}
      {L && !all.length && empty("No one here yet", "Press Start on the Run tab to find people, or add usernames below.")}
      {L && all.length > 0 && !matched.length && empty("No one matches", "Try another search or filter.")}
      <h4 className="mb-1 mt-6 text-sm font-semibold">Add people by username</h4>
      <p className="mb-2 text-sm text-mute">One username per line. People your team already contacted are skipped. Every message waits for your approval.</p>
      <Textarea id="pgimp" aria-label="Usernames to add" value={paste} onChange={(e) => setPaste(e.target.value)} />
      <Button className="mt-2" disabled={!paste.trim()} onClick={async () => { if (await pg.run(() => pg.op("leads_import", { text: paste }))) setPaste(""); }}>Import to queue</Button>
    </div>
  );

}

/* ---------------------------------------------------------------- Run */
export function Run() {
  const pg = usePg(); const confirm = useConfirm(); const [mode, setMode] = React.useState("find"); const [minutes, setMinutes] = React.useState(""); const [noScore, setNoScore] = React.useState(false);
  const doctor = pg.st?.last?.kind === "doctor" ? pg.st.last.out : null;
  const start = async () => {
    if (mode !== "find" && !(await confirm({ title: "Start sending messages?", body: modePlan(mode, pg.st), action: "Start agent" }))) return;
    pg.run(() => pg.job("run", { mode, minutes: parseFloat(minutes) || null }), "The agent started. It works in its own tab.");
  };
  return (
    <div>
      <Section title="Start the agent" hint="It works in its own tab. Your tabs are left alone. Every message waits for your approval, and sending stays within the daily limit and the team's shared history.">
        <div className="flex flex-wrap items-center gap-2">
          <Select id="pgmode" aria-label="What should the agent do?" value={mode} onChange={(e) => setMode(e.target.value)} className="w-64">
            {MODES.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
          </Select>
          <Input type="number" min={1} aria-label="Minutes to run" value={minutes} onChange={(e) => setMinutes(e.target.value)} placeholder="Minutes (optional)" className="w-44" />
          <Button variant="primary" disabled={pg.busy} onClick={start}><Play className="size-4 fill-current" aria-hidden /> Start</Button>
        </div>
        <p className="mt-2 text-sm text-mute">{modePlan(mode, pg.st)}</p>
      </Section>
      <Section title="Add the people on this page" hint="Adds everyone on the Reddit page you're looking at to the list.">
        <div className="mb-3"><Checkbox checked={noScore} onChange={setNoScore} label="Skip AI scoring (adds everyone, scored or not)" /></div>
        <Button disabled={pg.busy} onClick={() => pg.run(() => pg.job("scan", { no_score: noScore }), "Adding the people on this page…")}>Queue everyone on this page</Button>
      </Section>
      <Section title="Replies" hint="Look for answers to messages already sent.">
        <Button disabled={pg.busy} onClick={() => pg.run(() => pg.job("replies"), "Checking for replies…")}>Check replies</Button>
      </Section>
      <Section title="Check that Reddit works with the agent" hint="Safe: it only looks, it sends nothing. Run it once on a new account.">
        <Button disabled={pg.busy} onClick={() => pg.run(() => pg.job("doctor"), "Checking Reddit… The report appears here.")}>Run the check</Button>
        {doctor && <pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-hover/60 p-3 font-mono text-xs">{doctor}</pre>}
      </Section>
    </div>
  );
}

/* ---------------------------------------------------------------- Comments */
export function Comments() {
  const pg = usePg(); const confirm = useConfirm();
  const d = useData(async (p) => ({ O: (await p.op("opps_list")).opportunities as any[], I: (await p.op("ideas_list")).ideas as any[], P: (await p.op("posts_list")).posted as any[] }));
  const { O, I, P } = d ?? { O: [], I: [], P: [] };
  const [edits, setEdits] = React.useState<Record<string, string>>({});
  const post = async (o: any) => {
    const draft = edits[o.id] ?? o.draft;
    if (!(await confirm({ title: `Post this comment in r/${o.subreddit}?`, body: `It goes up now, from this account, on “${o.title}”.`, action: "Post comment" }))) return;
    await pg.run(async () => {
      if (draft !== o.draft) await pg.op("opp_edit", { id: o.id, draft });
      setEdits(({ [o.id]: _, ...r }) => r);
      return pg.job("post", { id: o.id });
    }, "Posting…");
  };
  return (
    <div>
      <h4 className="mb-2 text-sm font-semibold">Comment opportunities</h4>
      {O.map((o) => (
        <Card key={o.id} className="mb-2.5 p-3.5">
          <div className="flex flex-wrap items-center gap-2"><b>r/{o.subreddit}</b><span>{o.title}</span><Pill>{o.status}</Pill>
            <a className="inline-flex items-center gap-1 text-xs" href={o.post_url} target="_blank" rel="noreferrer">Thread <ExternalLink className="size-3" /></a></div>
          <p className="mt-1 text-sm text-mute">{o.why}</p>
          {o.status === "pending"
            ? <Textarea aria-label={`Comment for r/${o.subreddit}`} className="mt-2 min-h-[72px]" value={edits[o.id] ?? o.draft} onChange={(e) => setEdits({ ...edits, [o.id]: e.target.value })} />
            : <pre className="mt-2 whitespace-pre-wrap rounded-lg border border-line bg-hover/60 p-3 font-mono text-xs">{o.draft}</pre>}
          {o.status === "pending" && <div className="mt-2.5 flex gap-2">
            <Button size="sm" variant="primary" disabled={pg.busy || !(edits[o.id] ?? o.draft).trim()} onClick={() => post(o)}>Approve &amp; post</Button>
            <Button size="sm" onClick={() => pg.run(() => pg.op("opp_skip", { id: o.id }))}>Skip</Button></div>}
        </Card>
      ))}
      {!O.length && empty("No comment drafts yet", "They're drafted while the agent looks through your subreddits.")}
      <h4 className="mb-2 mt-6 text-sm font-semibold">Post ideas</h4>
      <Button size="sm" onClick={() => pg.run(() => pg.op("ideas_generate"), "Writing 5 post ideas…")}>Generate 5 post ideas</Button>
      {I.map((i) => (
        <Card key={i.id} className="my-2.5 p-3.5">
          <div><b>r/{i.subreddit}</b>: {i.title}</div>
          <pre className="mt-2 whitespace-pre-wrap rounded-lg border border-line bg-hover/60 p-3 font-mono text-xs">{i.body}</pre>
          <p className="mt-1 text-xs text-mute">{i.angle}</p>
          <div className="mt-2.5 flex flex-wrap gap-2">
            <Button size="sm" onClick={() => { navigator.clipboard.writeText(`${i.title}\n\n${i.body}`); toast.success("Copied"); }}><Copy aria-hidden /> Copy</Button>
            <a className={buttonStyles({ size: "sm" })} href={`https://www.reddit.com/r/${i.subreddit}/submit`} target="_blank" rel="noreferrer">Open submit page <ExternalLink aria-hidden /></a>
            <Button size="sm" variant="primary" disabled={pg.busy} onClick={async () => { if (await confirm({ title: `Post “${i.title}” to r/${i.subreddit}?`, body: "It goes up now, from this account.", action: "Post now" })) pg.run(() => pg.job("submit", { id: i.id }), "Posting…"); }}>Submit now</Button>
            <Button size="sm" onClick={() => pg.run(() => pg.op("idea_posted", { id: i.id }))}>I posted it myself</Button>
          </div>
        </Card>
      ))}
      {P.length > 0 && <><h4 className="mb-2 mt-6 text-sm font-semibold">Posted by the agent</h4>
        {P.map((x) => <div key={x.id} className="text-sm"><b>r/{x.subreddit}</b> {x.title} <a href={x.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1">Thread <ExternalLink className="size-3" /></a></div>)}</>}
    </div>
  );
}

/* ---------------------------------------------------------------- Messages */
export function Messages() {
  const pg = usePg();
  const d = useData(async (p) => ({ tpl: await p.op("template_show"), s: (await p.op("settings_set")).settings as Record<string, any> }));
  const [body, setBody] = React.useState<string | null>(null); const [intensity, setIntensity] = React.useState("medium");
  const [prev, setPrev] = React.useState<{ n: number; list: string[] } | null>(null);
  const show = (r: { variants?: number; previews?: string[] }) => { if (r?.previews) setPrev({ n: r.variants ?? r.previews.length, list: r.previews }); return r; };
  const s = d?.s ?? {}, tpl = d?.tpl ?? {};
  const text = body ?? tpl.template ?? "";
  const setting = (pairs: Record<string, unknown>) => pg.run(() => pg.op("settings_set", { pairs }), "");
  return (
    <div>
      <Section title="Who writes the messages" hint="Every message waits for your approval before it is sent, whoever wrote it.">
        <div className="flex flex-wrap items-center gap-2">
          <Button variant={s.dm_mode === "ai" ? "primary" : "secondary"} onClick={() => setting({ dm_mode: "ai" })}>AI writes each message</Button>
          <Button variant={s.dm_mode === "template" ? "primary" : "secondary"} onClick={() => setting({ dm_mode: "template" })}>Use my template</Button>
        </div>
      </Section>
      <Section title="Template" hint="Write {Hey|Hi|Hello} to pick one at random. Variables: {name}, {subreddit}, {topic}.">
        <Textarea id="pgtpl" className="min-h-28" value={text} onChange={(e) => setBody(e.target.value)} />
        <div className="mt-2.5 flex flex-wrap items-center gap-2">
          <Button variant="primary" disabled={body == null} onClick={async () => { if (await pg.run(() => pg.op("template_set", { body: text }), "Template saved")) setBody(null); }}>Save</Button>
          <Select aria-label="How many variations" value={intensity} onChange={(e) => setIntensity(e.target.value)} className="w-28"><option value="light">Light</option><option value="medium">Medium</option><option value="heavy">Heavy</option></Select>
          <Button onClick={() => pg.run(() => pg.op("template_spin", { intensity }).then(show), "Added variations to the template")}><Sparkles aria-hidden /> Add variations with AI</Button>
          <Button onClick={() => pg.run(() => pg.op("template_preview", { n: 4 }).then(show), "")}>Preview variations</Button>
          {tpl.undo && <Button onClick={() => pg.run(() => pg.op("template_undo"), "Template restored")}>Undo AI variations</Button>}
        </div>
        {prev && (
          <div className="mt-3 rounded-lg border border-line bg-hover/60 p-3 text-sm">
            <div className="mb-1.5 font-medium">{prev.n.toLocaleString()} possible versions. A few examples:</div>
            <ul className="list-disc space-y-1 pl-5">{prev.list.map((x, i) => <li key={i}>{x}</li>)}</ul>
          </div>
        )}
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
          <Button variant="primary" disabled={pg.busy} onClick={() => pg.run(() => pg.job("setup", { url: url ?? product?.url ?? "" }), "Reading your site and finding subreddits. This takes a minute or two.")}>{product ? "Re-analyze" : "Analyze my product"}</Button>
        </div>
        {product && (
          <div className="mt-3 rounded-lg border border-line bg-hover/50 p-3 text-sm">
            <b>{product.name}</b>: {product.summary}<br /><span className="text-mute">Who it's for:</span> {product.icp}<br /><span className="text-mute">Offer:</span> {product.offer}
          </div>
        )}
      </Section>
      <Section title="Subreddits">
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <Button size="sm" disabled={pg.busy || !product} onClick={() => pg.run(() => pg.job("discover"), "Finding subreddits…")}>Re-run discovery</Button>
          <Input density="sm" aria-label="Add a subreddit" value={add} onChange={(e) => setAdd(e.target.value)} placeholder="r/name" className="w-44" />
          <Button size="sm" disabled={!add.trim()} onClick={async () => { await pg.run(() => pg.op("subs_edit", { add: [add] }), ""); setAdd(""); }}>Add</Button>
        </div>
        <div className="divide-y divide-line rounded-lg border border-line">
          {subs.map((x) => (
            <div key={x.name} className="flex items-center gap-3 px-3 py-2 text-sm">
              <Switch aria-label={`Use r/${x.name}`} checked={!!x.enabled} onChange={(v) => pg.run(() => pg.op("subs_edit", { [v ? "enable" : "disable"]: [x.name] }), "")} />
              <b className="w-40 truncate">r/{x.name}</b><span className="text-mute">Score {x.score}</span><span className="text-mute">Rules risk: {x.rules_risk}</span>
              <span className="min-w-0 flex-1 truncate text-mute">{x.reason}</span>
              <IconButton size="sm" icon={X} label={`Remove r/${x.name}`} className="hover:text-bad" onClick={() => pg.run(() => pg.op("subs_edit", { remove: [x.name] }), "")} />
            </div>
          ))}
          {!subs.length && empty("No subreddits yet", "Analyze your product to find some, or add one above.")}
        </div>
      </Section>
    </div>
  );
}

/* ---------------------------------------------------------------- AI key */
/** The DeepSeek key lives on this computer only (never synced, never shown back in full). */
export function AiKey() {
  const pg = usePg(); const confirm = useConfirm();
  const [k, setK] = React.useState<{ set: boolean; hint: string } | null>(null);
  const [val, setVal] = React.useState(""); const [busy, setBusy] = React.useState(false); const [err, setErr] = React.useState("");
  const load = React.useCallback(() => api<{ set: boolean; hint: string }>("GET", "/pigeon/key").then(setK).catch(() => setK({ set: false, hint: "" })), []);
  React.useEffect(() => { load(); }, [load]);
  const save = async () => {
    setErr(""); setBusy(true);
    try { await api("POST", "/pigeon/key", { key: val.trim() }); setVal(""); toast.success("AI key saved on this computer"); await load(); }
    catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <Section title="AI key" hint="The agent uses DeepSeek to score people and write drafts. The key stays on this computer and is never synced to the team.">
      <form className="flex flex-wrap items-start gap-2" onSubmit={(e) => { e.preventDefault(); save(); }}>
        <div className="min-w-64 flex-1">
          <Input id="ai_key" type="password" autoComplete="off" spellCheck={false} value={val} onChange={(e) => setVal(e.target.value)}
            placeholder={k?.set ? `Saved key ending ${k.hint.replace("…", "")}. Paste a new one to replace it.` : "Paste your DeepSeek API key"} aria-invalid={!!err} aria-describedby="ai_key_msg" />
          <p id="ai_key_msg" className={`mt-1.5 text-xs ${err ? "text-bad" : "text-mute"}`}>{err || (k == null ? "Checking…" : k.set ? `A key is saved (${k.hint}).` : "No key saved yet.")}</p>
        </div>
        <Button type="submit" variant="primary" loading={busy} disabled={!val.trim()}>Save key</Button>
        {k?.set && <Button type="button" variant="danger" onClick={async () => {
          if (await confirm({ title: "Remove the AI key from this computer?", body: "The agent can't find people or write drafts until a key is added again.", action: "Remove key", danger: true }))
            pg.run(async () => { await api("DELETE", "/pigeon/key"); await load(); }, "AI key removed");
        }}>Remove</Button>}
      </form>
    </Section>
  );
}

/* ---------------------------------------------------------------- Settings */
const HIDDEN_SETTINGS = new Set(["warmup_start", "approve_ai_drafts"]); // approve_ai_drafts: every message now needs approval
export function SettingsTab() {
  return <div><AiKey /><AgentSettings /></div>;  // the key field stays mounted while settings load, so typing in it is never lost
}

type Def = { k: string; label: string; kind: "int" | "float" | "bool" | "hour" | "text" | "long" | "select"; min?: number; max?: number; step?: number; options?: [string, string][]; hint?: string };
/** The agent's settings, typed and grouped. Anything the backend has that isn't listed here is left alone (and shown under "Other"). */
const GROUPS: [string, string, Def[]][] = [
  ["Account", "Who this account is on Reddit.", [
    { k: "my_username", label: "This account's Reddit username", kind: "text", hint: "Shared with the team, so your other accounts never message it." },
  ]],
  ["Limits", "How much it sends. Lower is safer for a new account.", [
    { k: "daily_dm_cap", label: "Most messages per day", kind: "int", min: 1, max: 500 },
    { k: "warmup", label: "Start slowly (warm-up)", kind: "bool", hint: "Ramps from 5 a day up to the limit over about a week." },
    { k: "min_gap_sec", label: "Shortest wait between messages (seconds)", kind: "int", min: 10, max: 7200 },
    { k: "max_gap_sec", label: "Longest wait between messages (seconds)", kind: "int", min: 10, max: 14400 },
    { k: "posts_per_day", label: "Most posts per day", kind: "int", min: 0, max: 20 },
    { k: "upvotes", label: "Upvote posts while browsing", kind: "bool" },
    { k: "max_upvotes_per_session", label: "Most upvotes per run", kind: "int", min: 0, max: 200 },
  ]],
  ["Schedule", "When it works, and for how long.", [
    { k: "active_start", label: "Start work at", kind: "hour" },
    { k: "active_end", label: "Stop work at", kind: "hour" },
    { k: "session_minutes", label: "How long each run lasts (minutes)", kind: "int", min: 1, max: 600 },
    { k: "pace", label: "Speed (1 is normal, lower is slower)", kind: "float", min: 0.2, max: 3, step: 0.1 },
    { k: "auto_check_replies", label: "Check for replies automatically", kind: "bool" },
  ]],
  ["AI", "How people are scored and how drafts are written.", [
    { k: "dm_mode", label: "Who writes the messages", kind: "select", options: [["ai", "AI writes each one"], ["template", "My template"]] },
    { k: "min_fit", label: "Lowest match score to add someone (0 to 100)", kind: "int", min: 0, max: 100 },
    { k: "model", label: "AI model", kind: "text" },
    { k: "ai_guidance", label: "Extra instructions for the AI", kind: "long", hint: "For example: never mention pricing, keep it under 3 sentences." },
    { k: "use_team_setup", label: "Use the team's shared setup", kind: "bool", hint: "Adopt the product, subreddits and template the team publishes." },
  ]],
];
const KNOWN = new Set(GROUPS.flatMap(([, , ds]) => ds.map((d) => d.k)));
const hourLabel = (h: number) => `${String(h).padStart(2, "0")}:00`;

function problem(d: Def, v: unknown, all: Record<string, any>): string {
  if (d.kind === "int" || d.kind === "float") {
    const s = String(v ?? "").trim();
    if (s === "") return "Required.";
    const n = Number(s);
    if (!Number.isFinite(n) || (d.kind === "int" && !Number.isInteger(n))) return d.kind === "int" ? "Use a whole number." : "Use a number.";
    if ((d.min != null && n < d.min) || (d.max != null && n > d.max)) return `Between ${d.min} and ${d.max}.`;
    if (d.k === "max_gap_sec" && n < Number(all.min_gap_sec)) return "Must be at least the shortest wait.";
  }
  return "";
}

function AgentSettings() {
  const pg = usePg();
  const s = useData(async (p) => (await p.op("settings_set")).settings as Record<string, any>);
  const [draft, setDraft] = React.useState<Record<string, any>>({});
  const [busy, setBusy] = React.useState(false);
  const dirty = !!s && Object.entries(draft).some(([k, x]) => String(x) !== String(s[k]));
  React.useEffect(() => { pg.setDirty(dirty); }, [dirty, pg]);
  React.useEffect(() => () => pg.setDirty(false), [pg]);
  if (!s) return <SkeletonRows n={6} />;
  const all = { ...s, ...draft };
  const set = (k: string, x: unknown) => setDraft((d) => ({ ...d, [k]: x }));
  const defs = GROUPS.flatMap(([, , ds]) => ds).filter((d) => d.k in s);
  const errs = Object.fromEntries(defs.map((d) => [d.k, problem(d, all[d.k], all)]).filter(([, e]) => e));
  const save = async () => {
    const pairs: Record<string, unknown> = {};
    for (const d of defs) {
      if (!(d.k in draft) || String(draft[d.k]) === String(s[d.k])) continue;
      const x = draft[d.k];
      pairs[d.k] = d.kind === "int" || d.kind === "float" || d.kind === "hour" ? Number(x) : x;
    }
    setBusy(true);
    // the edits are cleared (and the drawer told) as soon as the server accepts them, before the toast shows
    await pg.run(async () => { const r = await pg.op("settings_set", { pairs }); setDraft({}); pg.setDirty(false); return r; }, "Settings saved");
    setBusy(false);
  };
  const control = (d: Def) => {
    const id = `set_${d.k}`, v = all[d.k], err = errs[d.k];
    const common = { id, "data-k": d.k, "aria-invalid": !!err || undefined, "aria-describedby": err || d.hint ? `${id}_msg` : undefined };
    switch (d.kind) {
      case "bool": return <Switch id={id} aria-label={d.label} checked={!!v} onChange={(x) => set(d.k, x)} />;
      case "hour": return <Select {...common} value={String(v)} onChange={(e) => set(d.k, e.target.value)} className="w-32">{Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{hourLabel(h)}</option>)}</Select>;
      case "select": return <Select {...common} value={String(v)} onChange={(e) => set(d.k, e.target.value)} className="w-56">{d.options!.map(([o, t]) => <option key={o} value={o}>{t}</option>)}</Select>;
      case "long": return <Textarea {...common} value={String(v ?? "")} onChange={(e) => set(d.k, e.target.value)} className="min-h-20" />;
      case "text": return <Input {...common} value={String(v ?? "")} onChange={(e) => set(d.k, e.target.value)} />;
      default: return <Input {...common} type="number" inputMode={d.kind === "int" ? "numeric" : "decimal"} min={d.min} max={d.max} step={d.step ?? 1} value={String(v ?? "")} onChange={(e) => set(d.k, e.target.value)} className="w-32" />;
    }
  };
  return (
    <form onSubmit={(e) => { e.preventDefault(); if (dirty && !Object.keys(errs).length) save(); }}>
      {GROUPS.map(([title, hint, ds]) => {
        const shown = ds.filter((d) => d.k in s);
        if (!shown.length) return null;
        return (
          <Card key={title} className="mb-3 p-4">
            <h4 className="font-semibold">{title}</h4><p className="mb-3 mt-0.5 text-sm text-mute">{hint}</p>
            <div className="space-y-3">
              {shown.map((d) => (
                <div key={d.k} className={d.kind === "long" ? "" : "grid items-center gap-x-4 gap-y-1 sm:grid-cols-[minmax(0,280px)_1fr]"}>
                  <label htmlFor={`set_${d.k}`} className="text-sm text-ink2">{d.label}</label>
                  <div className={d.kind === "long" ? "mt-1.5" : ""}>
                    {control(d)}
                    {(errs[d.k] || d.hint) && <p id={`set_${d.k}_msg`} className={`mt-1 text-xs ${errs[d.k] ? "text-bad" : "text-mute"}`}>{errs[d.k] || d.hint}</p>}
                  </div>
                </div>
              ))}
              {title === "Limits" && <Button type="button" size="sm" onClick={() => pg.run(() => pg.op("settings_set", { restart_warmup: true }), "Warm-up restarted from today")}>Restart warm-up from today</Button>}
            </div>
          </Card>
        );
      })}
      {Object.keys(s).some((k) => !KNOWN.has(k) && !HIDDEN_SETTINGS.has(k)) && (
        <p className="mb-3 text-xs text-mute">Other settings this version doesn&apos;t show: {Object.keys(s).filter((k) => !KNOWN.has(k) && !HIDDEN_SETTINGS.has(k)).join(", ")}.</p>
      )}
      <div className="sticky bottom-0 -mx-1 flex flex-wrap items-center gap-2 bg-card/95 px-1 py-3">
        <Button type="submit" variant="primary" loading={busy} disabled={!dirty || !!Object.keys(errs).length}>Save settings</Button>
        {dirty && <Button type="button" variant="ghost" onClick={() => setDraft({})}>Discard changes</Button>}
        <span className="text-sm text-mute">{Object.keys(errs).length ? "Fix the fields marked in red." : dirty ? "You have unsaved changes." : ""}</span>
      </div>
    </form>
  );
}

/* ---------------------------------------------------------------- Team activity */
export function TeamActivity() {
  const pg = usePg(); const confirm = useConfirm();
  const b = useData(async () => await api<any>("GET", "/pigeon/brain"));
  if (!b) return <SkeletonRows n={5} />;
  const k = b.summary.by_kind || {};
  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-2">
        <Button size="sm" onClick={() => pg.run(() => pg.op("brain_backfill"))}>Record this account's past DMs &amp; comments</Button>
        <Button size="sm" onClick={() => pg.run(() => pg.op("team_pull", { force: true }))}>Pull team setup</Button>
        <Button size="sm" onClick={async () => { if (await confirm({ title: "Share this account's setup with the team?", body: "The product, subreddits, template and shared settings replace the team's. Other accounts adopt them next time they run.", action: "Publish setup" })) pg.run(() => pg.op("team_publish")); }}>Publish this account&apos;s setup to the team</Button>
      </div>
      <p className="mb-3 text-sm text-mute">Team setup version {b.team_setup.version}{b.team_setup.product ? ` · ${b.team_setup.product}` : ""} · {b.team_setup.subreddits} subreddits · Limits: {Object.entries(b.team_setup.limits || {}).map(([a, x]) => `${LIMIT_WORDS[a] ?? a} ${x}`).join(", ") || "none"}</p>
      <p className="mb-3 text-sm"><b>Our accounts:</b> {b.accounts.map((a: any) => `u/${a.username} (${a.profile || "#" + a.profile_id})`).join(" · ") || <span className="text-mute">none registered yet</span>}</p>
      <Section title={`Team memory · last ${b.summary.days} days, every account`} hint={`${k.dm || 0} DMs · ${k.reply || 0} replies · ${k.comment || 0} comments · ${k.post || 0} posts · ${k.upvote || 0} upvotes${b.summary.stuck_claims ? ` · ${b.summary.stuck_claims} stuck claim(s)` : ""}`}>
        <Table cols={["Subreddit", N("DMs"), N("Replies"), N("Comments"), N("Posts"), N("Upvotes"), "Last"]} rows={b.summary.by_subreddit.map((r: any) => [`r/${r.subreddit}`, r.dms, r.replies, r.comments, r.posts, r.upvotes, <When key="w" t={r.last_at} />])} />
        <div className="h-3" />
        <Table cols={["Account", N("DMs"), N("Replies"), N("Comments"), N("Posts"), N("Upvotes")]} rows={b.summary.by_profile.map((r: any) => [r.profile || "-", r.dms, r.replies, r.comments, r.posts, r.upvotes])} />
      </Section>
      <h4 className="mb-2 mt-5 text-sm font-semibold">Recent</h4>
      <Table cols={["When", "What", "Status", "Account", "Where", "Who"]} rows={b.events.map((e: any) => [<When key="w" t={e.at} />, KIND_WORDS[e.kind] ?? e.kind, e.status, e.profile || "-", e.subreddit ? `r/${e.subreddit}` : "", e.target ? `u/${e.target}` : ""])} />
    </div>
  );
}
const N = (label: string) => ({ label, num: true });
const KIND_WORDS: Record<string, string> = { dm: "Message", reply: "Reply", comment: "Comment", post: "Post", upvote: "Upvote", lead: "Queued" };
const LIMIT_WORDS: Record<string, string> = { team_dm_per_day: "messages per day for the team", sub_dm_per_day: "messages per subreddit per day" };
function Table({ cols, rows }: { cols: (string | { label: string; num: boolean })[]; rows: React.ReactNode[][] }) {
  return <div className="rounded-lg border border-line"><DataTable dense cols={cols} rows={rows} empty={empty("Nothing yet")} /></div>;
}
