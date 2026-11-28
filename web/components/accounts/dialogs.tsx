"use client";
import * as React from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { usePigeon } from "@/lib/store";
import type { Profile } from "@/lib/types";
import { osName } from "@/lib/utils";
import { EmptyState } from "../ui/bits";
import { Button } from "../ui/button";
import { useConfirm } from "../ui/confirm";
import { emptyProxy, parseProxyUrl, ProxyFields, proxyProblem, proxyUrl, type ProxyForm } from "./proxy-fields";
import { Modal } from "../ui/dialog";
import { Field, Input, Select, Textarea } from "../ui/field";

const tagList = (v: string) => v.split(",").map((s) => s.trim()).filter(Boolean);
const canvasFor = (os: string, host: string) => (os === host ? "real" : "noise");

function Advanced({ os, setOs, canvas, setCanvas, host }: { os: string; setOs: (v: string) => void; canvas: string; setCanvas: (v: string) => void; host: string }) {
  return (
    <details className="group rounded-md border border-line px-3 py-2">
      <summary className="cursor-pointer select-none text-sm text-mute">Advanced: browser identity (the defaults are right for most people)</summary>
      <div className="mt-3 grid grid-cols-2 gap-3">
        <Field label="OS fingerprint">
          <Select value={os} onChange={(e) => { setOs(e.target.value); setCanvas(canvasFor(e.target.value, host)); }}>
            {["windows", "macos", "linux"].map((o) => <option key={o} value={o}>{osName(o)}{o === host ? " (this computer: best)" : ""}</option>)}
          </Select>
        </Field>
        <Field label="Canvas">
          <Select id="n_canvas" value={canvas} onChange={(e) => setCanvas(e.target.value)}>
            <option value="noise">Noise: random on every read, any OS</option><option value="real">Real: this computer's rendering</option>
          </Select>
        </Field>
      </div>
      <p className="mt-2 text-xs text-mute">An account that uses this computer's OS shows its real canvas and fonts, which passes checkers such as Pixelscan. Other OSes need canvas noise, and Pixelscan flags that.</p>
    </details>
  );
}

export function AddAccount({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { s, act } = usePigeon();
  const host = s.host_os;
  const [name, setName] = React.useState("");
  const [px, setPx] = React.useState<ProxyForm>(emptyProxy());
  const [os, setOs] = React.useState(host); const [canvas, setCanvas] = React.useState("real");
  const [busy, setBusy] = React.useState(false); const [err, setErr] = React.useState("");
  const tpl = (id: string) => {
    const t = s.templates?.find((x) => String(x.id) === id); if (!t) return;
    const x = t.settings as Record<string, any>;
    setOs(x.os || host); setCanvas(x.canvas || "noise");
    const parsed = x.proxy ? parseProxyUrl(String(x.proxy)) : null;
    if (parsed) setPx({ ...px, ...parsed });
  };
  async function go() {
    const problem = proxyProblem(px);
    if (problem) return setErr(problem);
    setBusy(true);
    const ok = await act(() => api("POST", "/profiles", { name: name.trim(), os, canvas, proxy: proxyUrl(px), proxy_name: px.name.trim(), start_url: "https://www.reddit.com/login" }), `Added ${name.trim()}`);
    setBusy(false); if (ok) { setName(""); setPx(emptyProxy()); setErr(""); onClose(); }
  }
  return (
    <Modal open={open} onOpenChange={(o) => !o && onClose()} title="Add an account" description="Each account gets its own browser identity that stays the same for its whole life.">
      <form noValidate onSubmit={(e) => { e.preventDefault(); go(); }} className="space-y-4">
        {!!s.templates?.length && <Field label="Template"><Select onChange={(e) => tpl(e.target.value)}><option value="">None</option>{s.templates.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</Select></Field>}
        <Field label="Name"><Input id="n_name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Reddit 1" autoFocus /></Field>
        <div className="rounded-lg border border-line p-3">
          <div className="mb-2 text-sm font-medium">Proxy <span className="font-normal text-mute">· strongly recommended, one per account</span></div>
          <ProxyFields value={px} onChange={(f) => { setPx(f); setErr(""); }} />
        </div>
        <Advanced os={os} setOs={setOs} canvas={canvas} setCanvas={setCanvas} host={host} />
        {err && <p role="alert" className="rounded-md bg-bad-bg px-3 py-2 text-sm text-bad">{err}</p>}
        <div className="flex justify-end gap-2 pt-1">
          <Button type="button" onClick={onClose}>Cancel</Button>
          <Button id="n_go" type="submit" variant="primary" loading={busy} disabled={!name.trim()}>{busy ? "Generating identity…" : "Create"}</Button>
        </div>
      </form>
    </Modal>
  );
}

export function AddMany({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { s, act } = usePigeon();
  const [f, setF] = React.useState({ count: "10", name: "Reddit {n}", proxies: "", tags: "reddit", url: "" });
  const [busy, setBusy] = React.useState(false);
  const lines = f.proxies.split("\n").filter((l) => l.trim()).length;
  async function go() {
    setBusy(true);
    const ok = await act(() => api("POST", "/profiles/bulk", { count: +f.count, name: f.name, proxies: f.proxies.split("\n"), tags: tagList(f.tags), start_url: f.url.trim() }), `Added ${f.count} accounts`);
    setBusy(false); if (ok) onClose();
  }
  return (
    <Modal open={open} onOpenChange={(o) => !o && onClose()} title="Add many accounts" description={`Each one gets its own identity. ${osName(s.host_os)} fingerprints use this computer's real canvas.`}>
      <form onSubmit={(e) => { e.preventDefault(); go(); }} className="space-y-4">
        <div className="grid grid-cols-2 gap-3">
          <Field label="How many"><Input type="number" min={1} max={500} value={f.count} onChange={(e) => setF({ ...f, count: e.target.value })} /></Field>
          <Field label="Name pattern ({n} = number)"><Input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        </div>
        <Field label="Proxies, one per line" hint={`${lines} ${lines === 1 ? "proxy" : "proxies"}. They are handed out in order, so each account gets a different one.`}>
          <Textarea mono value={f.proxies} onChange={(e) => setF({ ...f, proxies: e.target.value })} placeholder={"http://user:pass@host:port\nhttp://user:pass@host:port"} />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Tags"><Input value={f.tags} onChange={(e) => setF({ ...f, tags: e.target.value })} /></Field>
          <Field label="Start page"><Input value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} placeholder="https://" /></Field>
        </div>
        <div className="flex justify-end gap-2"><Button type="button" onClick={onClose}>Cancel</Button><Button type="submit" variant="primary" loading={busy}>Create {f.count}</Button></div>
      </form>
    </Modal>
  );
}

export function EditAccount({ p, onClose }: { p: Profile | null; onClose: () => void }) {
  const { act } = usePigeon(); const confirm = useConfirm();
  const [name, setName] = React.useState(""); const [canvas, setCanvas] = React.useState("real");
  const [px, setPx] = React.useState<ProxyForm>(emptyProxy());
  // The proxy (with its password) is fetched separately. Until it has loaded the fields and Save stay disabled, and the proxy is
  // only sent when the person changed it: an empty or failed load must never be saved as "no proxy".
  const [proxy, setProxy] = React.useState<{ state: "loading" | "ok" | "error"; orig: string; origName: string }>({ state: "loading", orig: "", origName: "" });
  const [busy, setBusy] = React.useState(false); const [err, setErr] = React.useState("");
  React.useEffect(() => {
    if (!p) return;
    let live = true;
    setName(p.name); setCanvas(p.canvas); setPx(emptyProxy()); setErr("");
    setProxy({ state: "loading", orig: "", origName: "" });
    api<{ proxy: string; name?: string }>("GET", `/profiles/${p.id}/proxy`)
      .then((r) => {
        if (!live) return;
        const f = { ...emptyProxy(), ...(parseProxyUrl(r.proxy || "") ?? {}), name: r.name || "" };
        setPx(f); setProxy({ state: "ok", orig: proxyUrl(f), origName: f.name });
      })
      .catch(() => live && setProxy({ state: "error", orig: "", origName: "" }));
    return () => { live = false; };
  }, [p]);
  if (!p) return null;
  const save = async () => {
    const body: Record<string, unknown> = { name: name.trim(), canvas };
    if (proxy.state === "ok") {
      const problem = proxyProblem(px);
      if (problem) return setErr(problem);
      if (proxyUrl(px) !== proxy.orig) body.proxy = proxyUrl(px);
      if (px.name.trim() !== proxy.origName || body.proxy !== undefined) { if (proxyUrl(px)) body.proxy_name = px.name.trim(); }
    }
    setBusy(true); const ok = await act(() => api("PATCH", `/profiles/${p.id}`, body), "Saved"); setBusy(false); if (ok) onClose();
  };
  const del = async () => {
    if (await confirm({ title: `Delete ${p.name}?`, body: "Its browser, cookies and agent data are deleted for the whole team. This can't be undone.", action: "Delete account", danger: true }))
      act(() => api("DELETE", `/profiles/${p.id}`), `Deleted ${p.name}`).then((ok) => ok && onClose());
  };
  return (
    <Modal open onOpenChange={(o) => !o && onClose()} title={`Edit ${p.name}`} description={`The ${osName(p.os)} fingerprint stays the same for this account's whole life.`}>
      <form noValidate onSubmit={(e) => { e.preventDefault(); save(); }} className="space-y-4">
        <Field label="Name"><Input id="e_name" value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <div className="rounded-lg border border-line p-3">
          <div className="mb-2 text-sm font-medium">Proxy <span className="font-normal text-mute">{proxy.state === "loading" ? "· loading the saved proxy…" : proxy.state === "error" ? "· couldn't load it, so it will be left as it is" : "· leave the host empty to use your own IP"}</span></div>
          <ProxyFields value={px} onChange={(f) => { setPx(f); setErr(""); }} disabled={proxy.state !== "ok"} />
        </div>
        <Field label="Canvas"><Select value={canvas} onChange={(e) => setCanvas(e.target.value)}><option value="noise">Noise</option><option value="real">Real</option></Select></Field>
        {err && <p role="alert" className="rounded-md bg-bad-bg px-3 py-2 text-sm text-bad">{err}</p>}
        <div className="flex items-center gap-2 pt-1">
          <Button type="button" variant="danger" onClick={del}>Delete</Button>
          <span className="flex-1" />
          <Button type="button" onClick={onClose}>Cancel</Button><Button type="submit" variant="primary" loading={busy} disabled={proxy.state === "loading"}>Save</Button>
        </div>
      </form>
    </Modal>
  );
}

export function ShareAccount({ p, onClose, onInvite }: { p: Profile | null; onClose: () => void; onInvite?: () => void }) {
  const { s, canAdmin } = usePigeon();
  const [shares, setShares] = React.useState<{ user_id: number; level: string }[]>([]);
  React.useEffect(() => { if (p) api("GET", `/profiles/${p.id}/shares`).then(setShares).catch((e) => toast.error(e.message)); }, [p]);
  if (!p) return null;
  const others = (s.members || []).filter((m) => m.role === "member");
  const set = async (uid: number, level: string) => {
    try { level ? await api("POST", `/profiles/${p.id}/shares`, { user_id: uid, level }) : await api("DELETE", `/profiles/${p.id}/shares/${uid}`);
      setShares(await api("GET", `/profiles/${p.id}/shares`)); } catch (e) { toast.error((e as Error).message); }
  };
  return (
    <Modal open onOpenChange={(o) => !o && onClose()} title={`Share ${p.name}`} description="Owners and admins see every account. Members see only the accounts they made or that are shared with them. “Can open” lets them use it; “Can edit” also lets them change its settings and sharing.">
      <div className="divide-y divide-line rounded-lg border border-line">
        {others.map((m) => {
          const cur = shares.find((x) => x.user_id === m.id)?.level || "";
          return (
            <div key={m.id} className="flex items-center justify-between gap-3 px-3 py-2.5">
              <span className="truncate text-sm">{m.email}</span>
              <Select aria-label={`Access for ${m.email}`} value={cur} onChange={(e) => set(m.id, e.target.value)} className="w-36"><option value="">No access</option><option value="run">Can open</option><option value="edit">Can edit</option></Select>
            </div>
          );
        })}
        {!others.length && <EmptyState compact title="No one to share with yet" body={<>Only members need sharing: owners and admins already see every account. {canAdmin ? <button type="button" className="font-medium text-ink underline underline-offset-4" onClick={() => { onClose(); onInvite?.(); }}>Invite someone</button> : "Ask an owner or admin to invite people."}</>} />}
      </div>
      <div className="mt-4 flex justify-end"><Button variant="primary" onClick={onClose}>Done</Button></div>
    </Modal>
  );
}

export function Cookies({ p, onClose }: { p: Profile | null; onClose: () => void }) {
  const { act } = usePigeon();
  const [text, setText] = React.useState(""); const [busy, setBusy] = React.useState(false);
  if (!p) return null;
  const imp = async () => { setBusy(true); const ok = await act(async () => { const r = await api<{ cookies: number }>("POST", `/profiles/${p.id}/cookies`, { text }); toast.success(`Imported ${r.cookies} cookies`); }); setBusy(false); if (ok) { setText(""); onClose(); } };
  const exp = () => act(async () => {
    const c = await api("GET", `/profiles/${p.id}/cookies`);
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([JSON.stringify(c, null, 2)], { type: "application/json" })); a.download = `cookies-${p.id}.json`; a.click();
  });
  return (
    <Modal open onOpenChange={(o) => !o && onClose()} title={`Cookies for ${p.name}`} description="Paste JSON (Cookie-Editor, EditThisCookie, Playwright) or a Netscape cookies.txt, or choose a file. Expiry dates more than 399 days out are shortened, as browsers do.">
      <input type="file" aria-label="Choose a cookies file" accept=".json,.txt" onChange={(e) => e.target.files?.[0]?.text().then(setText)} className="mb-3 block w-full text-sm text-mute file:mr-3 file:rounded-md file:border file:border-line2 file:bg-card file:px-3 file:py-1.5 file:text-ink" />
      <Textarea mono value={text} onChange={(e) => setText(e.target.value)} className="min-h-40" placeholder="Cookies" />
      <div className="mt-4 flex items-center gap-2">
        <Button onClick={exp}>Export as JSON</Button><span className="flex-1" />
        <Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={busy} disabled={!text.trim()} onClick={imp}>{busy ? "Opening account…" : "Import"}</Button>
      </div>
    </Modal>
  );
}
