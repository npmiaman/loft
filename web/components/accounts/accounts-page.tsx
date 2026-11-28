"use client";
import { ArrowDown, ArrowUp, Check, Download, Plus, Search, X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import * as React from "react";
import { cn, redditStatus } from "@/lib/utils";
import { api } from "@/lib/api";
import { usePigeon } from "@/lib/store";
import type { Profile } from "@/lib/types";
import { Callout, Card, EmptyState, PageHead, Pill } from "../ui/bits";
import { Checkbox } from "../ui/checkbox";
import { useConfirm } from "../ui/confirm";
import { Modal } from "../ui/dialog";
import { Field, Input, Select } from "../ui/field";
import { Button, IconButton } from "../ui/button";
import { PigeonDrawer } from "../pigeon/drawer";
import { useNav } from "../shell";
import { AccountRow, COLS, accountStatus } from "./account-row";
import { AddAccount, Cookies, EditAccount, ShareAccount } from "./dialogs";

function Steps({ has, opened, piged }: { has: boolean; opened: boolean; piged: boolean }) {
  const step = (n: number, done: boolean, title: string, text: string) => (
    <li key={n} data-step={n} data-done={done ? "true" : "false"} className="flex gap-3">
      <span className={`grid size-6 shrink-0 place-items-center rounded-full border text-xs font-semibold ${done ? "border-ok-solid bg-ok-solid text-on-ok" : "border-line2 text-mute"}`}>
        {done ? <motion.span initial={{ scale: 0 }} animate={{ scale: 1 }} transition={{ type: "spring", stiffness: 500, damping: 20 }}><Check className="size-3.5" aria-hidden /></motion.span> : n}
      </span>
      <div><div className={`font-semibold ${done ? "text-mute line-through decoration-line2" : ""}`}>{title}</div><div className="text-sm text-mute">{text}</div></div>
    </li>
  );
  return (
    <ol className="grid gap-4 md:grid-cols-3">
      {step(1, has, "Add an account", "Give it a name and its own proxy.")}
      {step(2, opened, "Open it and sign in to Reddit", "Press Open browser, sign in to Reddit once, then Close & save.")}
      {step(3, piged, "Start the agent", "It finds people and drafts messages. You approve them before anything is sent.")}
    </ol>
  );
}

type SortKey = "name" | "reddit";
const SHOW = { all: "All accounts", open: "Open now", running: "Agent running", noproxy: "No proxy", error: "Couldn't open" } as const;

function BulkTag({ open, onClose, onAdd }: { open: boolean; onClose: () => void; onAdd: (t: string) => void }) {
  const [t, setT] = React.useState("");
  React.useEffect(() => { if (open) setT(""); }, [open]);
  return (
    <Modal open={open} onOpenChange={(o) => !o && onClose()} title="Add a tag" description="Added to every selected account. Existing tags stay.">
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); if (t.trim()) onAdd(t.trim()); }}>
        <Field label="Tag"><Input id="bulk_tag" value={t} onChange={(e) => setT(e.target.value)} placeholder="warm" autoFocus /></Field>
        <div className="flex justify-end gap-2"><Button type="button" onClick={onClose}>Cancel</Button><Button type="submit" variant="primary" disabled={!t.trim()}>Add tag</Button></div>
      </form>
    </Modal>
  );
}

export function AccountsPage() {
  const { s, canAdmin, act } = usePigeon(); const nav = useNav(); const confirm = useConfirm();
  const ps = s.profiles ?? [];
  const [q, setQ] = React.useState("");
  const [dlg, setDlg] = React.useState<null | "add" | "many">(null);
  const [edit, setEdit] = React.useState<Profile | null>(null); const [share, setShare] = React.useState<Profile | null>(null); const [cookies, setCookies] = React.useState<Profile | null>(null);
  const [pg, setPg] = React.useState<number | null>(null);
  const [modes, setModes] = React.useState<Record<number, string>>({});
  const [off, setOff] = React.useState(true);
  React.useEffect(() => setOff(localStorage.getItem("pigeon.ob.off") === "1"), []);

  const has = ps.length > 0, opened = ps.some((p) => redditStatus(p).state === "connected"); // step 2 is done once an account is really signed in to Reddit
  const piged = Object.values(s.jobs ?? {}).some((j) => j.pigeon && (j.pigeon.state !== "idle" || j.pigeon.stats.leads > 0));
  const [show, setShow] = React.useState<keyof typeof SHOW>("all"); const [tag, setTag] = React.useState<string | null>(null);
  const [sel, setSel] = React.useState<Set<number>>(new Set()); const [tagDlg, setTagDlg] = React.useState(false);
  const kind = (p: Profile) => {
    const j = s.jobs?.[`run-${p.id}`];
    return { open: p.running_here, running: !!(p.running_here && j?.pigeon && j.pigeon.state !== "idle"), noproxy: !p.proxy, error: accountStatus(p, j).tone === "bad" };
  };
  const shown = ps.filter((p) => (!q || `${p.name} ${p.tags.join(" ")} ${p.notes}`.toLowerCase().includes(q.toLowerCase()))
    && (show === "all" || kind(p)[show]) && (!tag || p.tags.includes(tag)));
  const [sort, setSort] = React.useState<{ key: SortKey | null; dir: 1 | -1 }>({ key: null, dir: 1 });
  const rank = (p: Profile): number => {
    if (sort.key === "reddit") return { connected: 0, unknown: 1, disconnected: 2 }[redditStatus(p).state];
    return 0;
  };
  const sorted = sort.key ? [...shown].sort((a, b) => (sort.key === "name" ? a.name.localeCompare(b.name, undefined, { numeric: true }) : rank(a) - rank(b) || a.name.localeCompare(b.name, undefined, { numeric: true })) * sort.dir) : shown;
  const count = (k: Exclude<keyof typeof SHOW, "all">) => ps.filter((p) => kind(p)[k]).length;
  const nErr = count("error");
  const fetchJob = s.jobs?.fetch;
  // a selection only ever holds accounts that still exist and are on screen
  React.useEffect(() => { setSel((x) => { const ids = new Set(shown.map((p) => p.id)); const n = new Set([...x].filter((i) => ids.has(i))); return n.size === x.size ? x : n; }); }, [shown]);
  const picked = ps.filter((p) => sel.has(p.id));
  const toggle = (id: number) => setSel((x) => { const n = new Set(x); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const allOn = shown.length > 0 && shown.every((p) => sel.has(p.id));
  const bulk = async (label: string, list: Profile[], fn: (p: Profile) => Promise<unknown>, keep = false) => {
    if (!list.length) return;
    const ok = await act(async () => { for (const p of list) await fn(p); }, label);
    if (ok && !keep) setSel(new Set());
  };
  const bulkDelete = async () => {
    const n = picked.length;
    if (await confirm({ title: `Delete ${n === 1 ? picked[0].name : `${n} accounts`}?`, body: <>{picked.map((p) => p.name).join(", ")}: browsers, cookies and agent data are deleted for the whole team. This can&apos;t be undone.</>, action: n === 1 ? "Delete account" : `Delete ${n} accounts`, danger: true }))
      bulk(`Deleted ${n === 1 ? picked[0].name : `${n} accounts`}`, picked, (p) => api("DELETE", `/profiles/${p.id}`));
  };

  return (
    <>
      <PageHead title="Accounts" sub="Each account has its own browser and its own proxy."
        actions={<>
          <Button variant="primary" onClick={() => setDlg("add")}><Plus aria-hidden /> Add account</Button>
        </>} />

      {!s.browser && (
        <div className="mb-4"><Callout tone="warn" action={<Button variant="primary" loading={!!fetchJob?.alive} onClick={() => act(() => api("POST", "/browser/install"))}><Download aria-hidden /> {fetchJob?.alive ? "Downloading…" : "Install browser"}</Button>}>
          <b>The browser isn't installed.</b> <span className="text-mute">It's a one-time ~1.3 GB download.</span>
          {fetchJob && <pre className="mt-1 max-h-24 overflow-auto font-mono text-xs text-mute">{fetchJob.log.join("\n")}</pre>}
        </Callout></div>
      )}

      {has && !off && !(has && opened && piged) && (
        <Card className="mb-5 p-5">
          <div className="mb-3 flex items-center justify-between"><b>Get started</b>
            <Button size="sm" variant="ghost" onClick={() => { localStorage.setItem("pigeon.ob.off", "1"); setOff(true); }}>Dismiss</Button></div>
          <Steps has={has} opened={opened} piged={piged} />
        </Card>
      )}

      {!has ? (
        <Card>
          <EmptyState title="No accounts yet" body="An account is one Reddit login with its own browser and its own proxy. Add the first one to begin."
            action={<Button variant="primary" onClick={() => setDlg("add")}><Plus aria-hidden /> Add your first account</Button>} />
          <div className="mx-auto max-w-3xl px-6 pb-10 text-left"><Steps has={false} opened={false} piged={false} /></div>
        </Card>
      ) : (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <div className="relative w-full sm:w-64"><Search className="absolute left-3 top-2.5 size-4 text-mute" aria-hidden />
              <Input type="search" aria-label="Search accounts" placeholder="Search accounts" value={q} onChange={(e) => setQ(e.target.value)} className="pl-9" /></div>
            <Select aria-label="Show" value={show} onChange={(e) => setShow(e.target.value as keyof typeof SHOW)} className="w-full sm:w-56">
              {Object.entries(SHOW).map(([k, t]) => <option key={k} value={k}>{t}</option>)}
            </Select>
            {tag && <Pill className="gap-1 pr-0.5">Tag: {tag}<IconButton size="sm" icon={X} label={`Stop filtering by ${tag}`} className="size-6" onClick={() => setTag(null)} /></Pill>}
          </div>
          <p className="mb-3 text-sm text-mute">{ps.length === 1 ? "1 account" : `${ps.length} accounts`} · {count("open")} open · {count("noproxy")} no proxy{nErr ? ` · ${nErr} couldn't open` : ""}{shown.length !== ps.length ? ` · showing ${shown.length}` : ""}</p>
          {sel.size > 0 && (
            <div className="sticky top-14 z-10 mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-line bg-card px-3 py-2 shadow-card md:top-2">
              <span className="text-sm font-medium">{sel.size} selected</span>
              <span className="flex-1" />
              <Button size="sm" disabled={!picked.some((p) => kind(p).open && !kind(p).running)} onClick={() => bulk("Starting the agents", picked.filter((p) => kind(p).open && !kind(p).running), (p) => api("POST", `/profiles/${p.id}/pigeon`, { action: "start", mode: "find" }))}>Start agent (find people)</Button>
              <Button size="sm" disabled={!picked.some((p) => p.running_here)} onClick={() => bulk("Closing and saving", picked.filter((p) => p.running_here), (p) => api("POST", `/profiles/${p.id}/stop`))}>Close &amp; save</Button>
              <Button size="sm" disabled={!picked.every((p) => p.access === "edit")} onClick={() => setTagDlg(true)}>Add tag</Button>
              <Button size="sm" variant="danger" disabled={!picked.every((p) => p.access === "edit") || picked.some((p) => p.running_here)} onClick={bulkDelete}>Delete</Button>
              <Button size="sm" variant="ghost" onClick={() => setSel(new Set())}>Clear selection</Button>
            </div>
          )}
          <Card role="table" aria-label="Accounts">
            {shown.length > 0 && (
              <div role="row" data-head className={cn("sticky top-14 z-[5] rounded-t-lg border-b border-line bg-card px-4 py-2 text-xs font-medium text-mute md:top-0", COLS)}>
                <span role="columnheader" className="inline-flex items-center gap-2"><Checkbox aria-label="Select all shown" checked={allOn} onChange={(v) => setSel(v ? new Set(shown.map((p) => p.id)) : new Set())} /><span className="md:hidden">Select all</span></span>
                {([["name", "Account"], ["reddit", "Reddit"], [null, "Proxy"]] as [SortKey | null, string][]).map(([k, label]) => (
                  <span key={label} role="columnheader" aria-sort={k && sort.key === k ? (sort.dir === 1 ? "ascending" : "descending") : undefined} className="hidden md:block">
                    {k ? (
                      <button type="button" onClick={() => setSort((x) => ({ key: k, dir: x.key === k ? (-x.dir as 1 | -1) : 1 }))} className="inline-flex items-center gap-1 hover:text-ink">
                        {label}{sort.key === k && (sort.dir === 1 ? <ArrowUp className="size-3" aria-hidden /> : <ArrowDown className="size-3" aria-hidden />)}
                      </button>
                    ) : label}
                  </span>
                ))}
                <span role="columnheader" className="hidden md:block"><span className="sr-only">Actions</span></span>
              </div>
            )}
            <AnimatePresence initial={false}>
              {sorted.map((p) => (
                <AccountRow key={p.id} p={p} mode={modes[p.id] ?? "find"} setMode={(m) => setModes({ ...modes, [p.id]: m })} hasRunners={!!s.runners?.length} canAdmin={canAdmin}
                  selected={sel.has(p.id)} onSelect={() => toggle(p.id)} onTag={setTag}
                  onPigeon={() => setPg(p.id)} onEdit={() => setEdit(p)} onShare={() => setShare(p)} onCookies={() => setCookies(p)} />
              ))}
            </AnimatePresence>
            {!shown.length && <EmptyState compact title={q ? `No accounts match “${q}”` : "No accounts match these filters"}
              action={<Button size="sm" onClick={() => { setQ(""); setShow("all"); setTag(null); }}>Show all accounts</Button>} />}
          </Card>
          <BulkTag open={tagDlg} onClose={() => setTagDlg(false)} onAdd={(t) => { setTagDlg(false); bulk(`Tagged ${picked.length === 1 ? picked[0].name : `${picked.length} accounts`} “${t}”`, picked.filter((p) => !p.tags.includes(t)), (p) => api("PATCH", `/profiles/${p.id}`, { tags: [...p.tags, t] }), true); }} />
        </>
      )}

      <AddAccount open={dlg === "add"} onClose={() => setDlg(null)} />
      <EditAccount p={edit} onClose={() => setEdit(null)} />
      <ShareAccount p={share} onClose={() => setShare(null)} onInvite={() => nav("settings", "members")} />
      <Cookies p={cookies} onClose={() => setCookies(null)} />
      <PigeonDrawer id={pg} onClose={() => setPg(null)} />
    </>
  );
}
