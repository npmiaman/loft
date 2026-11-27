"use client";
import * as React from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { useLoft } from "@/lib/store";
import type { Profile } from "@/lib/types";
import { Button } from "../ui/button";
import { Modal } from "../ui/dialog";
import { Field, Input, Select, Textarea } from "../ui/field";

const tagList = (v: string) => v.split(",").map((s) => s.trim()).filter(Boolean);
const canvasFor = (os: string, host: string) => (os === host ? "real" : "noise");

function Advanced({ os, setOs, canvas, setCanvas, host }: { os: string; setOs: (v: string) => void; canvas: string; setCanvas: (v: string) => void; host: string }) {
  return (
    <details className="group rounded-lg border border-line px-3 py-2">
      <summary className="cursor-pointer select-none text-[13px] text-mute">Advanced: browser identity (the defaults are right for most people)</summary>
      <div className="mt-3 grid grid-cols-2 gap-3">
        <Field label="OS fingerprint">
          <Select value={os} onChange={(e) => { setOs(e.target.value); setCanvas(canvasFor(e.target.value, host)); }}>
            {["windows", "macos", "linux"].map((o) => <option key={o} value={o}>{o}{o === host ? " (this machine: best)" : ""}</option>)}
          </Select>
        </Field>
        <Field label="Canvas">
          <Select id="n_canvas" value={canvas} onChange={(e) => setCanvas(e.target.value)}>
            <option value="noise">noise: random per read, any OS</option><option value="real">real: this machine's rendering</option>
          </Select>
        </Field>
      </div>
      <p className="mt-2 text-xs text-mute">An account that uses this machine's OS shows its real canvas and fonts, which passes checkers such as Pixelscan. Other OSes need canvas noise, and Pixelscan flags that.</p>
    </details>
  );
}

export function AddAccount({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { s, act } = useLoft();
  const host = s.host_os;
  const [f, setF] = React.useState({ name: "", proxy: "", url: "", tags: "", notes: "" });
  const [os, setOs] = React.useState(host); const [canvas, setCanvas] = React.useState("real");
  const [busy, setBusy] = React.useState(false);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  const tpl = (id: string) => {
    const t = s.templates?.find((x) => String(x.id) === id); if (!t) return;
    const x = t.settings as Record<string, any>;
    setOs(x.os || host); setCanvas(x.canvas || "noise"); setF({ ...f, proxy: x.proxy || "", url: x.start_url || "", tags: (x.tags || []).join(", ") });
  };
  async function go() {
    setBusy(true);
    const ok = await act(() => api("POST", "/profiles", { name: f.name.trim(), os, canvas, proxy: f.proxy.trim(), start_url: f.url.trim(), notes: f.notes, tags: tagList(f.tags) }), `Added ${f.name.trim()}`);
    setBusy(false); if (ok) { setF({ name: "", proxy: "", url: "", tags: "", notes: "" }); onClose(); }
  }
  return (
    <Modal open={open} onOpenChange={(o) => !o && onClose()} title="Add an account" description="Each account gets its own browser identity that stays the same for its whole life.">
      <form onSubmit={(e) => { e.preventDefault(); go(); }} className="space-y-4">
        {!!s.templates?.length && <Field label="Template"><Select onChange={(e) => tpl(e.target.value)}><option value="">none</option>{s.templates.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</Select></Field>}
        <Field label="Name"><Input id="n_name" value={f.name} onChange={set("name")} placeholder="Reddit 1" autoFocus /></Field>
        <Field label="Proxy (strongly recommended: one per account)" hint={<>Format <code className="rounded bg-hover px-1">http://user:pass@host:port</code>. For a gateway, put <code className="rounded bg-hover px-1">{"{pid}"}</code> in the username to give this browser its own sticky exit.</>}>
          <Input id="n_proxy" value={f.proxy} onChange={set("proxy")} placeholder="http://user:pass@1.2.3.4:8080" />
        </Field>
        <Field label="Start page"><Input value={f.url} onChange={set("url")} placeholder="https://www.reddit.com/" /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Tags (comma separated)"><Input value={f.tags} onChange={set("tags")} placeholder="reddit" /></Field>
          <Field label="Notes"><Input value={f.notes} onChange={set("notes")} /></Field>
        </div>
        <Advanced os={os} setOs={setOs} canvas={canvas} setCanvas={setCanvas} host={host} />
        <div className="flex justify-end gap-2 pt-1">
          <Button type="button" onClick={onClose}>Cancel</Button>
          <Button id="n_go" type="submit" variant="primary" loading={busy} disabled={!f.name.trim()}>{busy ? "Generating identity…" : "Create"}</Button>
        </div>
      </form>
    </Modal>
  );
}

export function AddMany({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { s, act } = useLoft();
  const [f, setF] = React.useState({ count: "10", name: "Reddit {n}", proxies: "", tags: "reddit", url: "" });
  const [busy, setBusy] = React.useState(false);
  const lines = f.proxies.split("\n").filter((l) => l.trim()).length;
  async function go() {
    setBusy(true);
    const ok = await act(() => api("POST", "/profiles/bulk", { count: +f.count, name: f.name, proxies: f.proxies.split("\n"), tags: tagList(f.tags), start_url: f.url.trim() }), `Added ${f.count} accounts`);
    setBusy(false); if (ok) onClose();
  }
  return (
    <Modal open={open} onOpenChange={(o) => !o && onClose()} title="Add many accounts" description={`Each one gets its own identity. ${s.host_os} fingerprints use real canvas.`}>
      <form onSubmit={(e) => { e.preventDefault(); go(); }} className="space-y-4">
        <div className="grid grid-cols-2 gap-3">
          <Field label="How many"><Input type="number" min={1} max={500} value={f.count} onChange={(e) => setF({ ...f, count: e.target.value })} /></Field>
          <Field label="Name pattern ({n} = number)"><Input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        </div>
        <Field label="Proxies, one per line" hint={`${lines} proxy line(s). They are handed out in order, so each account gets a different one.`}>
          <Textarea value={f.proxies} onChange={(e) => setF({ ...f, proxies: e.target.value })} placeholder={"http://user:pass@host:port\nhttp://user:pass@host:port"} />
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
  const { act } = useLoft();
  const [f, setF] = React.useState({ name: "", proxy: "", url: "", notes: "", canvas: "real" });
  const [busy, setBusy] = React.useState(false);
  React.useEffect(() => {
    if (!p) return;
    setF({ name: p.name, proxy: "", url: p.start_url || "", notes: p.notes, canvas: p.canvas });
    api<{ proxy: string }>("GET", `/profiles/${p.id}/proxy`).then((r) => setF((x) => ({ ...x, proxy: r.proxy || "" }))).catch((e) => toast.error(e.message));
  }, [p]);
  if (!p) return null;
  const save = async () => { setBusy(true); const ok = await act(() => api("PATCH", `/profiles/${p.id}`, { name: f.name, proxy: f.proxy, canvas: f.canvas, start_url: f.url, notes: f.notes }), "Saved"); setBusy(false); if (ok) onClose(); };
  return (
    <Modal open onOpenChange={(o) => !o && onClose()} title={`Edit ${p.name}`} description={`The ${p.os} fingerprint is fixed for this account's life.`}>
      <form onSubmit={(e) => { e.preventDefault(); save(); }} className="space-y-4">
        <Field label="Name"><Input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Proxy (empty = your own IP)"><Input value={f.proxy} onChange={(e) => setF({ ...f, proxy: e.target.value })} placeholder="http://user:pass@host:port" /></Field>
        <Field label="Canvas"><Select value={f.canvas} onChange={(e) => setF({ ...f, canvas: e.target.value })}><option value="noise">noise</option><option value="real">real</option></Select></Field>
        <Field label="Start page"><Input value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} /></Field>
        <Field label="Notes"><Input value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
        <div className="flex items-center gap-2 pt-1">
          <Button type="button" variant="danger" onClick={() => { if (confirm("Delete this account for the whole team?")) act(() => api("DELETE", `/profiles/${p.id}`), "Deleted").then((ok) => ok && onClose()); }}>Delete</Button>
          <span className="flex-1" />
          <Button type="button" onClick={onClose}>Cancel</Button><Button type="submit" variant="primary" loading={busy}>Save</Button>
        </div>
      </form>
    </Modal>
  );
}

export function ShareAccount({ p, onClose }: { p: Profile | null; onClose: () => void }) {
  const { s } = useLoft();
  const [shares, setShares] = React.useState<{ user_id: number; level: string }[]>([]);
  React.useEffect(() => { if (p) api("GET", `/profiles/${p.id}/shares`).then(setShares).catch((e) => toast.error(e.message)); }, [p]);
  if (!p) return null;
  const others = (s.members || []).filter((m) => m.role === "member");
  const set = async (uid: number, level: string) => {
    try { level ? await api("POST", `/profiles/${p.id}/shares`, { user_id: uid, level }) : await api("DELETE", `/profiles/${p.id}/shares/${uid}`);
      setShares(await api("GET", `/profiles/${p.id}/shares`)); } catch (e) { toast.error((e as Error).message); }
  };
  return (
    <Modal open onOpenChange={(o) => !o && onClose()} title={`Share ${p.name}`} description="Owners and admins see every account. Members see only accounts they made or that are shared with them. Run can open it; Edit can also change settings and sharing.">
      <div className="divide-y divide-line rounded-lg border border-line">
        {others.map((m) => {
          const cur = shares.find((x) => x.user_id === m.id)?.level || "";
          return (
            <div key={m.id} className="flex items-center justify-between gap-3 px-3 py-2.5">
              <span className="truncate text-[13.5px]">{m.email}</span>
              <Select value={cur} onChange={(e) => set(m.id, e.target.value)} className="w-36"><option value="">no access</option><option value="run">run</option><option value="edit">edit</option></Select>
            </div>
          );
        })}
        {!others.length && <p className="px-3 py-4 text-[13px] text-mute">No members to share with. Invite people from the Team page.</p>}
      </div>
      <div className="mt-4 flex justify-end"><Button variant="primary" onClick={onClose}>Done</Button></div>
    </Modal>
  );
}

export function Cookies({ p, onClose }: { p: Profile | null; onClose: () => void }) {
  const { act } = useLoft();
  const [text, setText] = React.useState(""); const [busy, setBusy] = React.useState(false);
  if (!p) return null;
  const imp = async () => { setBusy(true); const ok = await act(async () => { const r = await api<{ cookies: number }>("POST", `/profiles/${p.id}/cookies`, { text }); toast.success(`Imported ${r.cookies} cookies`); }); setBusy(false); if (ok) { setText(""); onClose(); } };
  const exp = () => act(async () => {
    const c = await api("GET", `/profiles/${p.id}/cookies`);
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([JSON.stringify(c, null, 2)], { type: "application/json" })); a.download = `cookies-${p.id}.json`; a.click();
  });
  return (
    <Modal open onOpenChange={(o) => !o && onClose()} title={`Cookies for ${p.name}`} description="Paste JSON (Cookie-Editor, EditThisCookie, Playwright) or a Netscape cookies.txt, or choose a file. Expiry dates more than 399 days out are shortened, as browsers do.">
      <input type="file" accept=".json,.txt" onChange={(e) => e.target.files?.[0]?.text().then(setText)} className="mb-3 block w-full text-[13px] text-mute file:mr-3 file:rounded-lg file:border file:border-line2 file:bg-card file:px-3 file:py-1.5 file:text-ink" />
      <Textarea value={text} onChange={(e) => setText(e.target.value)} className="min-h-40" placeholder="Cookies" />
      <div className="mt-4 flex items-center gap-2">
        <Button onClick={exp}>Export as JSON</Button><span className="flex-1" />
        <Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={busy} disabled={!text.trim()} onClick={imp}>{busy ? "Opening account…" : "Import"}</Button>
      </div>
    </Modal>
  );
}
