"use client";
import { Check, CircleHelp, Globe, MoreHorizontal, Pencil, Play, Plus, Square, X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import * as React from "react";
import { api } from "@/lib/api";
import { usePigeon } from "@/lib/store";
import type { Job, Profile } from "@/lib/types";
import { cn, friendlyError, modePlan, msgs, people, pigeonWords, redditStatus } from "@/lib/utils";
import { Checkbox } from "../ui/checkbox";
import { MODES } from "../pigeon/tabs";
import { useConfirm } from "../ui/confirm";
import { Avatar, Dot, Pill } from "../ui/bits";
import { Button, IconButton } from "../ui/button";
import { Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger } from "../ui/dropdown";
import { Field, Input, Select } from "../ui/field";
import { Modal } from "../ui/dialog";
import { emptyProxy, parseProxyUrl, ProxyFields, proxyProblem, proxyUrl, type ProxyForm } from "./proxy-fields";

export function accountStatus(p: Profile, j?: Job) {
  if (p.running_here) return { tone: "ok" as const, text: "Open on this computer", sub: j?.pigeon ? pigeonWords(j.pigeon) : "Starting…" };
  if (p.running) return { tone: "warn" as const, text: `${p.running.by} is using it`, sub: `on ${p.running.device}` };
  const err = j && !j.alive ? j.log.find((l) => /^error:/i.test(l)) : undefined;
  if (err) return { tone: "bad" as const, text: "Couldn't open", sub: "", err: err.replace(/^error:\s*/i, "") };
  return { tone: "idle" as const, text: p.version ? "Closed · saved" : "Not opened yet", sub: "" };
}


/** One column template for the header and every row, so they line up: select, account, Reddit, proxy, status, tags, actions. */
export const COLS = "md:grid md:grid-cols-[22px_minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)_300px] md:items-center md:gap-3"; // minmax(0,…): tracks depend only on the table width, so every row lines up

/** The account's name; the pencil renames it in place (Enter saves, Escape cancels). */
function AccountName({ p }: { p: Profile }) {
  const { act } = usePigeon();
  const [editing, setEditing] = React.useState(false);
  const [val, setVal] = React.useState(p.name);
  const save = async () => {
    const name = val.trim();
    if (!name || name === p.name) return setEditing(false);
    if (await act(() => api("PATCH", `/profiles/${p.id}`, { name }), "Renamed")) setEditing(false);
  };
  if (editing) {
    return (
      <form className="flex items-center gap-2" onSubmit={(e) => { e.preventDefault(); void save(); }}>
        <Input id={`rename_${p.id}`} autoFocus maxLength={80} value={val} aria-label={`New name for ${p.name}`} className="max-w-64"
          onChange={(e) => setVal(e.target.value)} onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); setEditing(false); } }} />
        <Button type="submit" size="sm" variant="primary">Save</Button>
        <Button type="button" size="sm" variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
      </form>
    );
  }
  return (
    <div className="flex min-w-0 items-center gap-0.5">
      <span className="truncate font-medium">{p.name}</span>
      {p.access === "edit" && <IconButton icon={Pencil} size="sm" label={`Rename ${p.name}`} onClick={() => { setVal(p.name); setEditing(true); }} className="shrink-0 text-mute md:opacity-0 md:group-hover:opacity-100 md:focus-visible:opacity-100" />}
    </div>
  );
}

/** Add, change or remove the account's proxy, under a name. The address is only ever shown here, never in the list. */
function ProxyDialog({ p, onClose }: { p: Profile; onClose: () => void }) {
  const { act } = usePigeon(); const confirm = useConfirm();
  const [px, setPx] = React.useState<ProxyForm>({ ...emptyProxy(), name: p.proxy?.name ?? "" });
  const [loaded, setLoaded] = React.useState(!p.proxy);
  const [err, setErr] = React.useState("");
  React.useEffect(() => {
    if (!p.proxy) return;
    api<{ proxy: string; name: string }>("GET", `/profiles/${p.id}/proxy`)
      .then((r) => { setPx({ ...emptyProxy(), ...(parseProxyUrl(r.proxy || "") ?? {}), name: r.name || "" }); setLoaded(true); })
      .catch((e) => setErr((e as Error).message));
  }, [p.id, p.proxy]);
  const save = async () => {
    if (!px.host.trim()) return setErr("Enter the proxy's host.");
    const problem = proxyProblem(px);
    if (problem) return setErr(problem);
    if (await act(() => api("PATCH", `/profiles/${p.id}`, { proxy: proxyUrl(px), proxy_name: px.name.trim() }), "Proxy saved")) onClose();
  };
  const remove = async () => {
    if (!(await confirm({ title: `Remove the proxy from ${p.name}?`, body: "The account will use your own IP until you add one again.", action: "Remove proxy", danger: true }))) return;
    if (await act(() => api("PATCH", `/profiles/${p.id}`, { proxy: "" }), "Proxy removed")) onClose();
  };
  return (
    <Modal open onOpenChange={(o) => !o && onClose()} title={p.proxy ? `Proxy for ${p.name}` : `Add a proxy to ${p.name}`}
      description="Each account should have its own proxy. Stored encrypted; only its name shows in the account list.">
      <form noValidate className="space-y-4" onSubmit={(e) => { e.preventDefault(); void save(); }}>
        <ProxyFields value={px} onChange={(f) => { setPx(f); setErr(""); }} disabled={!loaded} />
        {err && <p role="alert" className="rounded-md bg-bad-bg px-3 py-2 text-sm text-bad">{err}</p>}
        <div className="flex flex-wrap items-center justify-between gap-2">
          {p.proxy ? <Button type="button" variant="danger" onClick={remove}>Remove proxy</Button> : <span />}
          <div className="flex gap-2"><Button type="button" onClick={onClose}>Cancel</Button><Button type="submit" variant="primary" disabled={!loaded}>Save proxy</Button></div>
        </div>
      </form>
    </Modal>
  );
}

export function AccountRow({ p, mode, setMode, onPigeon, onEdit, onShare, onCookies, hasRunners, canAdmin, selected, onSelect, onTag }: {
  p: Profile; mode: string; setMode: (m: string) => void; onPigeon: () => void; onEdit: () => void; onShare: () => void; onCookies: () => void;
  hasRunners: boolean; canAdmin: boolean; selected?: boolean; onSelect?: () => void; onTag?: (t: string) => void;
}) {
  const { s, act } = usePigeon(); const confirm = useConfirm();
  const j = s.jobs?.[`run-${p.id}`];
  const st = accountStatus(p, j);
  const pg = p.running_here ? j?.pigeon : null;
  const busy = !!p.running || p.running_here;
  const [raw, setRaw] = React.useState(false);
  const [proxyOpen, setProxyOpen] = React.useState(false);
  // An action stays "in progress" from the click until the app reports the new state (the list refreshes every 3 s),
  // so a click never looks like it did nothing. It also ends if the request fails, or after a minute at most.
  const [pending, setPending] = React.useState<null | "open" | "close" | "start" | "stop">(null);
  const pgState = pg?.state ?? null;
  React.useEffect(() => {
    if (!pending) return;
    const done = (pending === "open" && (p.running_here || (j && !j.alive))) || (pending === "close" && !p.running_here)
      || (pending === "start" && pgState && pgState !== "idle") || (pending === "stop" && (!pgState || pgState === "idle"));
    if (done) { setPending(null); return; }
    const t = setTimeout(() => setPending(null), 60000);
    return () => clearTimeout(t);
  }, [pending, p.running_here, j, pgState]);
  const go = async (kind: NonNullable<typeof pending>, fn: () => Promise<unknown>) => {
    setPending(kind);
    if (!(await act(fn))) setPending(null);
  };
  const start = async () => {
    if (mode !== "find" && !(await confirm({ title: `Start sending from ${p.name}?`, body: modePlan(mode, pg), action: "Start agent" }))) return;
    go("start", () => api("POST", `/profiles/${p.id}/pigeon`, { action: "start", mode }));
  };
  // The window opens before the request, inside the click, so popup blockers allow it; it is pointed at the viewer once the runner answers.
  const cloud = () => {
    const w = window.open("", "_blank");
    act(async () => {
      try { const r = await api<{ id: string; view: string }>("POST", `/profiles/${p.id}/cloud`, {}); if (w) w.location.href = r.view; else window.open(r.view, "_blank"); }
      catch (e) { w?.close(); throw e; }
    });
  };
  const unlock = async () => {
    if (await confirm({ title: `Force unlock ${p.name}?`, body: `${p.running?.by ?? "Someone"} has it open. Changes they haven't saved yet will be rejected when they close it.`, action: "Force unlock", danger: true }))
      act(() => api("POST", `/profiles/${p.id}/unlock`));
  };

  const r = redditStatus(p);
  const tagsShown = p.tags.slice(0, 2);
  return (
    <motion.div layout="position" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.15 }}
      role="row" data-account={p.name}
      className={cn("group border-b border-line px-4 py-2.5 transition-colors last:border-b-0 hover:bg-hover/45", COLS, selected && "bg-hover/60")}>
      {/* account */}
      <div role="cell" className="flex min-w-0 items-center gap-2.5 md:contents">
        {onSelect && <span className="flex items-center"><Checkbox aria-label={`Select ${p.name}`} checked={!!selected} onChange={onSelect} /></span>}
        <div className="flex min-w-0 flex-1 items-center gap-2" title={p.notes || undefined}>
          <Avatar name={p.name} size={26} />
          <AccountName p={p} />
          {/* status only when it's worth acting on; closed accounts show nothing */}
          {(p.running_here || p.running || st.tone === "bad") && (
            <span data-status title={[st.text, st.sub, pg?.stats ? `found ${people(pg.stats.leads)}, sent ${msgs(pg.stats.dms)}` : ""].filter(Boolean).join(" · ")}
              className={cn("inline-flex h-5 shrink-0 items-center gap-1 truncate rounded-full px-1.5 text-xs font-medium",
                p.running_here ? "bg-ok-bg text-ok" : st.tone === "bad" ? "bg-bad-bg text-bad" : "bg-warn-bg text-warn")}>
              <Dot tone={p.running_here ? "ok" : st.tone === "bad" ? "bad" : "warn"} />
              <span className="truncate">{p.running_here ? "Open" : st.tone === "bad" ? "Couldn't open" : `In use by ${p.running?.by}`}</span>
            </span>
          )}
          {tagsShown.map((t) => <button key={t} type="button" title={`Show accounts tagged ${t}`} onClick={() => onTag?.(t)} className="shrink-0 rounded-full"><Pill className="h-5 px-2 hover:bg-line">{t}</Pill></button>)}
          {p.tags.length > tagsShown.length && <span className="shrink-0 text-xs text-mute" title={p.tags.slice(tagsShown.length).join(", ")}>+{p.tags.length - tagsShown.length}</span>}
        </div>
      </div>
      {/* reddit */}
      <div role="cell" className="mt-2 inline-flex md:mt-0">
        <span data-reddit={r.state} title={r.title}
          className={cn("inline-flex h-6 max-w-full items-center gap-1 truncate rounded-full px-2 text-xs font-medium",
            r.state === "connected" ? "bg-ok-bg text-ok" : r.state === "disconnected" ? "bg-bad-bg text-bad" : "border border-line text-mute")}>
          <span data-icon={r.state === "connected" ? "tick" : r.state === "disconnected" ? "cross" : "unknown"} className="grid shrink-0 place-items-center" aria-hidden>
            {r.state === "connected" ? <Check className="size-3.5" /> : r.state === "disconnected" ? <X className="size-3.5" /> : <CircleHelp className="size-3.5" />}
          </span>
          <span className="truncate">{r.state === "disconnected" ? <><span className="md:hidden">Reddit </span>{"Not connected"}</> : r.text}</span>
        </span>
      </div>
      {/* proxy */}
      <div role="cell" className="ml-2 mt-2 inline-flex md:ml-0 md:mt-0">
        <button type="button" data-proxy={p.proxy ? "set" : "none"} onClick={() => setProxyOpen(true)} disabled={p.access !== "edit"}
          title={p.proxy ? "Edit this account's proxy" : "No proxy: this account uses your own IP. Add one."}
          className={cn("inline-flex h-6 max-w-full items-center gap-1 truncate rounded-full border px-2 text-xs font-medium transition-colors disabled:pointer-events-none",
            p.proxy ? "border-line text-ink2 hover:bg-hover" : "border-dashed border-warn/60 text-warn hover:bg-warn-bg")}>
          {p.proxy ? <Globe className="size-3.5 shrink-0" aria-hidden /> : <Plus className="size-3.5 shrink-0" aria-hidden />}
          <span className="truncate">{p.proxy ? (p.proxy.name || "Proxy") : "Add proxy"}</span>
        </button>
      </div>
      {/* actions */}
      <div role="cell" className="mt-2 flex flex-wrap items-center gap-1.5 md:mt-0 md:justify-end">
        {!p.running_here ? (
          <Button size="sm" variant="primary" disabled={busy && pending !== "open"} loading={pending === "open"} onClick={() => go("open", () => api("POST", `/profiles/${p.id}/run`))}>{pending === "open" ? "Opening…" : <><Play className="fill-current" aria-hidden /> Open browser</>}</Button>
        ) : (
          <>
            {pg && (pg.state !== "idle"
              ? <Button size="sm" loading={pending === "stop" || pg.state === "stopping"} onClick={() => go("stop", () => api("POST", `/profiles/${p.id}/pigeon`, { action: "stop" }))}>{pending === "stop" || pg.state === "stopping" ? "Stopping…" : "Stop agent"}</Button>
              : <>
                  <Select aria-label="What should the agent do?" title={modePlan(mode, pg)} value={mode} onChange={(e) => setMode(e.target.value)} className="w-36">
                    {MODES.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
                  </Select>
                  <Button size="sm" variant="primary" loading={pending === "start"} onClick={start}>{pending === "start" ? "Starting…" : "Start agent"}</Button>
                  <p className="w-full text-xs text-mute md:order-last md:text-right">{modePlan(mode, pg)}</p>
                </>)}
            {pg && <Button size="sm" onClick={onPigeon}>Agent controls</Button>}
            <Button size="sm" loading={pending === "close"} onClick={() => go("close", () => api("POST", `/profiles/${p.id}/stop`))} title="Closes the browser and saves its cookies to the team">{pending === "close" ? "Closing…" : <><Square className="fill-current" aria-hidden /> Close &amp; save</>}</Button>
          </>
        )}
        <Menu>
          <MenuTrigger asChild><IconButton icon={MoreHorizontal} size="sm" label={`More for ${p.name}`} /></MenuTrigger>
          <MenuContent>
            {p.access === "edit" && <><MenuItem onSelect={onEdit}>Edit</MenuItem><MenuItem onSelect={onShare}>Share</MenuItem></>}
            <MenuItem disabled={busy} onSelect={onCookies}>Import or export cookies</MenuItem>
            {hasRunners && <MenuItem disabled={busy} onSelect={cloud}>Run on a cloud runner</MenuItem>}
            {p.running && canAdmin && !p.running_here && (
              <><MenuSeparator /><MenuItem danger onSelect={unlock}>Force unlock</MenuItem></>
            )}
          </MenuContent>
        </Menu>
      </div>
      {/* problems and the raw log span the whole row */}
      {(st.err || (j && j.log.length > 0)) && (
        <div className="col-span-full mt-1.5 md:pl-[38px]">
          {st.err && <div role="alert" className="rounded-md bg-bad-bg px-2.5 py-1.5 text-xs text-bad">{friendlyError(st.err)}</div>}
          {j && j.log.length > 0 && (
            <div className="mt-1">
              <button onClick={() => setRaw(!raw)} className="text-xs text-mute hover:text-ink">{raw ? "Hide details" : "Details"}</button>
              {raw && <pre className="mt-1 max-w-xl whitespace-pre-wrap font-mono text-xs text-mute">{j.log.join("\n")}</pre>}
            </div>
          )}
        </div>
      )}
      {proxyOpen && <ProxyDialog p={p} onClose={() => setProxyOpen(false)} />}
    </motion.div>
  );
}
