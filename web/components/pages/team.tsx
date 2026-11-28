"use client";
import { Copy, KeyRound, Trash2, X } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";
import { api, apiFile, saveFile } from "@/lib/api";
import { usePigeon } from "@/lib/store";
import { osName } from "@/lib/utils";
import { Callout, Card, EmptyState, PageHead, Pill } from "../ui/bits";
import { Button, IconButton } from "../ui/button";
import { useConfirm } from "../ui/confirm";
import { Modal } from "../ui/dialog";
import { Field, Input, Select } from "../ui/field";
import { DataTable } from "../ui/table";

const ROLE_WORDS: Record<string, string> = { owner: "Owner", admin: "Admin", member: "Member" };
type Member = { id: number; name: string; email: string; role: string };
type Preview = { running: { name: string; device: string }[]; profiles: number; rekey: { email: string }[]; invites_to_revoke: number; without_key: { email: string }[] };

/** Inside a Settings pane the pane already shows the title and description, so a Section shows only its action and content. */
export const InPane = React.createContext(false);

export function Section({ title, hint, children, action }: { title: string; hint?: React.ReactNode; children: React.ReactNode; action?: React.ReactNode }) {
  if (React.useContext(InPane)) return <section>{action && <div className="mb-3 flex justify-end">{action}</div>}{children}</section>;
  return (
    <section className="mt-10 first:mt-0">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div><h2 className="text-lg font-semibold tracking-tight">{title}</h2>{hint && <p className="mt-0.5 text-sm text-mute">{hint}</p>}</div>
        {action}
      </div>
      {children}
    </section>
  );
}
const copy = (text: string, what: string) => navigator.clipboard.writeText(text).then(() => toast.success(`${what} copied`), () => toast.error("Couldn't copy. Select the text and copy it by hand."));
const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"}`;

/** Rotate the team key, after showing exactly what it will do. Shared by the button and by "remove member". */
function useRotate() {
  const { act } = usePigeon(); const confirm = useConfirm();
  return async (why?: string) => {
    let pv: Preview;
    try { pv = await api<Preview>("GET", "/rotate-key/preview"); } catch (e) { toast.error((e as Error).message); return; }
    if (pv.running.length) { toast.error(`Close these accounts first: ${pv.running.map((r) => `${r.name} (on ${r.device})`).join(", ")}.`); return; }
    const lost = pv.without_key.length ? ` ${pv.without_key.map((m) => m.email).join(", ")} can't be given the new key and will need a new invite.` : "";
    if (!(await confirm({
      title: "Rotate the team key now?",
      body: `${why ? why + " " : ""}This re-encrypts ${plural(pv.profiles, "account")} and every template, gives the new key to ${plural(pv.rekey.length, "member")}, and cancels ${plural(pv.invites_to_revoke, "unused invite")}.${lost}`,
      action: "Rotate key", danger: true,
    }))) return;
    await act(async () => {
      const r = await api<{ rekeyed: unknown[]; invites_revoked: number; without_key: unknown[] }>("POST", "/rotate-key");
      toast.success(`Key rotated. ${plural(r.rekeyed.length, "member")} got the new key, ${plural(r.invites_revoked, "unused invite")} cancelled.`);
    });
  };
}

export function Invites() {
  const [role, setRole] = React.useState("member"); const [code, setCode] = React.useState(""); const [busy, setBusy] = React.useState(false);
  const make = async () => {
    setBusy(true);
    try { setCode((await api<{ code: string }>("POST", "/invite", { role })).code); } catch (e) { toast.error((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <Card className="mb-4 p-4">
      <div className="flex flex-wrap items-end gap-2">
        <Field label="Invite someone as" className="w-40">
          <Select aria-label="Role for the invite" value={role} onChange={(e) => setRole(e.target.value)}><option value="member">Member</option><option value="admin">Admin</option></Select>
        </Field>
        <Button variant="primary" loading={busy} onClick={make}>Create invite</Button>
        <p className="w-full text-xs text-mute sm:w-auto sm:flex-1">Members see only the accounts shared with them. Admins see every account and can manage the team.</p>
      </div>
      {code && (
        <div className="mt-3 rounded-md border border-line bg-hover/60 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <code className="min-w-0 flex-1 break-all font-mono text-xs">{code}</code>
            <Button size="sm" onClick={() => copy(code, "Invite code")}><Copy aria-hidden /> Copy code</Button>
          </div>
          <p className="mt-2 text-xs text-mute">Send it privately. It works once.</p>
        </div>
      )}
    </Card>
  );
}

export function Members() {
  const { s, act, canAdmin } = usePigeon(); const confirm = useConfirm(); const rotate = useRotate();
  const owner = s.role === "owner";
  const teamName = s.me?.teams.find((t) => t.id === s.team)?.name ?? "the team";
  const setRole = async (m: Member, role: string) => {
    if (role === m.role) return;
    if (role === "owner" || m.role === "owner") {
      const up = role === "owner";
      if (!(await confirm({
        title: up ? `Make ${m.email} an owner?` : `Change ${m.email} from owner to ${ROLE_WORDS[role].toLowerCase()}?`,
        body: up ? "Owners can do everything, including removing people and rotating the team's key." : "They lose owner rights straight away.",
        action: up ? "Make owner" : `Make ${ROLE_WORDS[role].toLowerCase()}`, danger: !up,
      }))) return;
    }
    act(() => api("POST", `/members/${m.id}/role`, { role }), `${m.email} is now ${ROLE_WORDS[role].toLowerCase()}`);
  };
  const remove = async (m: Member) => {
    if (!(await confirm({ title: `Remove ${m.email} from ${teamName}?`, body: "They lose access to the team's accounts on the server straight away.", action: "Remove", danger: true }))) return;
    if (await act(() => api("DELETE", `/members/${m.id}`), `Removed ${m.email}`)) rotate(`${m.email} still has a copy of the current key.`);
  };
  const can = (m: Member) => canAdmin && m.id !== s.me?.id && (owner || m.role !== "owner");
  return (
    <Card>
      <DataTable cols={["Member", "Role", { label: "", className: "w-px" }]} empty={<EmptyState compact title="No members yet" />}
        rows={(s.members ?? []).map((m) => [
          <span key="n">{m.name || m.email} {m.name && <span className="text-xs text-mute">{m.email}</span>}{m.id === s.me?.id && <span className="text-xs text-mute"> (you)</span>}</span>,
          can(m)
            ? <Select key="r" aria-label={`Role for ${m.email}`} value={m.role} onChange={(e) => setRole(m, e.target.value)} className="w-32">
                {["member", "admin", ...(owner || m.role === "owner" ? ["owner"] : [])].map((v) => <option key={v} value={v}>{ROLE_WORDS[v]}</option>)}
              </Select>
            : <Pill key="r">{ROLE_WORDS[m.role] ?? m.role}</Pill>,
          can(m) ? <Button key="x" size="sm" variant="danger" aria-label={`Remove ${m.email}`} onClick={() => remove(m)}>Remove</Button> : null,
        ])} />
    </Card>
  );
}

export function Security() {
  const { act } = usePigeon(); const rotate = useRotate();
  const [dlg, setDlg] = React.useState<null | "backup" | "restore">(null);
  const [pw, setPw] = React.useState(""); const [file, setFile] = React.useState<File | null>(null); const [busy, setBusy] = React.useState(false);
  const close = () => { setDlg(null); setPw(""); setFile(null); };
  const backup = async () => {
    setBusy(true);
    try { const r = await apiFile("POST", "/backup", { passphrase: pw }); saveFile(r.blob, r.name || "pigeon-team-backup.pigeonbackup"); toast.success("Backup downloaded"); close(); }
    catch (e) { toast.error((e as Error).message); } finally { setBusy(false); }
  };
  const restore = async () => {
    if (!file) return;
    setBusy(true);
    const buf = new Uint8Array(await file.arrayBuffer()); let bin = "";
    for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
    const ok = await act(async () => {
      const r = await api<Record<string, number>>("POST", "/restore", { data: btoa(bin), passphrase: pw });
      toast.success(`Restored${r && typeof r.profiles === "number" ? ` ${plural(r.profiles, "account")}` : ""}`);
    });
    setBusy(false); if (ok) close();
  };
  return (
    <>
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => rotate()}><KeyRound aria-hidden /> Rotate team key</Button>
        <Button onClick={() => setDlg("backup")}>Back up team</Button>
        <Button onClick={() => setDlg("restore")}>Restore backup</Button>
      </div>
      <p className="mt-2 text-sm text-mute">Rotating makes a new key, re-encrypts everything and locks out anyone removed. A backup is one passphrase-protected file with every account.</p>
      <Modal open={dlg === "backup"} onOpenChange={(o) => !o && close()} title="Back up the team" description="One file with every account and template, encrypted with this passphrase. You need it to restore.">
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); backup(); }}>
          <Field label="Passphrase" hint={pw && pw.length < 8 ? "Use at least 8 characters." : "Keep it somewhere safe. Without it the file can't be opened."}>
            <Input id="bk_pw" type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} aria-invalid={!!pw && pw.length < 8} />
          </Field>
          <div className="flex justify-end gap-2"><Button type="button" onClick={close}>Cancel</Button><Button type="submit" variant="primary" loading={busy} disabled={pw.length < 8}>Download backup</Button></div>
        </form>
      </Modal>
      <Modal open={dlg === "restore"} onOpenChange={(o) => !o && close()} title="Restore a team backup" description="Adds the backup's accounts and templates to this team. Existing accounts are never deleted.">
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); restore(); }}>
          <Field label="Backup file"><input id="rb_file" type="file" accept=".pigeonbackup,.loftbackup" onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="block w-full text-sm text-mute file:mr-3 file:rounded-md file:border file:border-line2 file:bg-card file:px-3 file:py-1.5 file:text-ink" /></Field>
          <Field label="Passphrase"><Input id="rb_pw" type="password" value={pw} onChange={(e) => setPw(e.target.value)} /></Field>
          <div className="flex justify-end gap-2"><Button type="button" onClick={close}>Cancel</Button><Button type="submit" variant="primary" loading={busy} disabled={!file || !pw}>Restore</Button></div>
        </form>
      </Modal>
    </>
  );
}

export function Templates() {
  const { s, act, canAdmin } = usePigeon(); const confirm = useConfirm();
  const [open, setOpen] = React.useState(false);
  const blank = { name: "", os: s.host_os, canvas: "real", proxy: "", start_url: "", tags: "" };
  const [f, setF] = React.useState(blank);
  const save = async () => {
    const ok = await act(() => api("POST", "/templates", { name: f.name.trim(), os: f.os, canvas: f.canvas, proxy: f.proxy.trim(), start_url: f.start_url.trim(), tags: f.tags.split(",").map((t) => t.trim()).filter(Boolean) }), `Saved ${f.name.trim()}`);
    if (ok) { setOpen(false); setF(blank); }
  };
  const del = async (t: { id: number; name: string }) => {
    if (await confirm({ title: `Delete the template ${t.name}?`, body: "Accounts made from it keep their settings.", action: "Delete template", danger: true }))
      act(() => api("DELETE", `/templates/${t.id}`), `Deleted ${t.name}`);
  };
  const desc = (x: Record<string, any>) => [osName(x.os), `${x.canvas === "real" ? "real" : "noise"} canvas`, x.proxy ? "proxy" : "", (x.tags || []).join(", ")].filter(Boolean).join(" · ");
  return (
    <Section title="Templates" hint="Starting settings for new accounts. Stored encrypted." action={canAdmin && <Button onClick={() => setOpen(true)}>New template</Button>}>
      <Card>
        <DataTable cols={["Name", "Settings", { label: "", className: "w-px" }]} empty={<EmptyState compact title="No templates yet" />}
          rows={(s.templates ?? []).map((t) => [t.name, <span key="d" className="text-mute">{desc(t.settings)}</span>,
            canAdmin ? <IconButton key="x" size="sm" icon={Trash2} label={`Delete template ${t.name}`} onClick={() => del(t)} /> : null])} />
      </Card>
      <Modal open={open} onOpenChange={setOpen} title="New template" description="New accounts can start from these settings.">
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); save(); }}>
          <Field label="Name"><Input id="t_name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="EU accounts" /></Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="OS"><Select value={f.os} onChange={(e) => setF({ ...f, os: e.target.value })}>{["windows", "macos", "linux"].map((o) => <option key={o} value={o}>{osName(o)}</option>)}</Select></Field>
            <Field label="Canvas"><Select value={f.canvas} onChange={(e) => setF({ ...f, canvas: e.target.value })}><option value="real">Real</option><option value="noise">Noise</option></Select></Field>
          </div>
          <Field label="Proxy (optional)"><Input value={f.proxy} onChange={(e) => setF({ ...f, proxy: e.target.value })} placeholder="http://user:pass@host:port" /></Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Start page"><Input value={f.start_url} onChange={(e) => setF({ ...f, start_url: e.target.value })} placeholder="https://www.reddit.com/" /></Field>
            <Field label="Tags (comma separated)"><Input value={f.tags} onChange={(e) => setF({ ...f, tags: e.target.value })} /></Field>
          </div>
          <div className="flex justify-end gap-2"><Button type="button" onClick={() => setOpen(false)}>Cancel</Button><Button type="submit" variant="primary" disabled={!f.name.trim()}>Save template</Button></div>
        </form>
      </Modal>
    </Section>
  );
}

export function Runners() {
  const { s, act, canAdmin } = usePigeon(); const confirm = useConfirm();
  const [f, setF] = React.useState({ name: "", url: "", key: "" });
  const add = async () => { if (await act(() => api("POST", "/runners", { name: f.name.trim(), url: f.url.trim(), key: f.key.trim() }), `Added ${f.name.trim()}`)) setF({ name: "", url: "", key: "" }); };
  const del = async (r: { id: number; name: string }) => {
    if (await confirm({ title: `Remove the runner ${r.name}?`, body: "Accounts running on it now keep running until they are stopped.", action: "Remove runner", danger: true }))
      act(() => api("DELETE", `/runners/${r.id}`), `Removed ${r.name}`);
  };
  return (
    <Section title="Cloud runners" hint="Servers that run accounts for you, viewable from any browser or phone.">
      <Card>
        <DataTable cols={["Name", "Address", { label: "", className: "w-px" }]} empty={<EmptyState compact title="No runners yet" />}
          rows={(s.runners ?? []).map((r) => [r.name, <span key="u" className="break-all text-mute">{r.url}</span>,
            canAdmin ? <IconButton key="x" size="sm" icon={X} label={`Remove runner ${r.name}`} onClick={() => del(r)} /> : null])} />
      </Card>
      {canAdmin && (
        <form className="mt-3 grid gap-2 sm:grid-cols-[160px_1fr_1fr_auto]" onSubmit={(e) => { e.preventDefault(); add(); }}>
          <Input id="r_name" aria-label="Runner name" placeholder="Name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
          <Input id="r_url" aria-label="Runner address" placeholder="https://runner:4420" value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} />
          <Input id="r_key" aria-label="Runner key" type="password" placeholder="Runner key" value={f.key} onChange={(e) => setF({ ...f, key: e.target.value })} />
          <Button type="submit" disabled={!f.name.trim() || !f.url.trim() || !f.key.trim()}>Add runner</Button>
        </form>
      )}
    </Section>
  );
}

export function Automation() {
  const [k, setK] = React.useState<{ key: string; base: string } | null>(null); const [show, setShow] = React.useState(false); const [err, setErr] = React.useState("");
  React.useEffect(() => { api<{ key: string; base: string }>("GET", "/apikey").then(setK).catch((e) => setErr(e.message)); }, []);
  return (
    <Section title="Automation API" hint="Scripts on this computer can manage accounts through a local API.">
      <Card className="space-y-2 p-4 text-sm">
        {err ? <p className="text-bad">{err}</p> : !k ? <p className="text-mute">Loading…</p> : (
          <>
            <div>Address: <code className="font-mono text-xs">{k.base}</code></div>
            <div className="flex flex-wrap items-center gap-2">Key: <code className="font-mono text-xs">{show ? k.key : "•".repeat(16)}</code>
              <Button size="sm" variant="ghost" onClick={() => setShow(!show)}>{show ? "Hide key" : "Show key"}</Button>
              <Button size="sm" variant="ghost" onClick={() => copy(k.key, "API key")}><Copy aria-hidden /> Copy key</Button></div>
            <p className="text-xs text-mute">Send it in an X-API-Key header, for example to {k.base}/profiles.</p>
          </>
        )}
      </Card>
    </Section>
  );
}

export function OtherTeams() {
  const { act } = usePigeon(); const [code, setCode] = React.useState(""); const [name, setName] = React.useState("");
  return (
    <Section title="Teams" hint="Join another team with an invite code, or start a new one. Switch between teams from the menu at the top of the sidebar.">
      <Card className="grid gap-4 p-4 sm:grid-cols-2">
        <form onSubmit={(e) => { e.preventDefault(); act(() => api("POST", "/join", { code: code.trim() }), "Joined").then((ok) => ok && setCode("")); }}>
          <Field label="Join with an invite code"><div className="flex gap-2"><Input value={code} onChange={(e) => setCode(e.target.value)} /><Button type="submit" disabled={!code.trim()}>Join</Button></div></Field>
        </form>
        <form onSubmit={(e) => { e.preventDefault(); act(() => api("POST", "/teams", { name: name.trim() }), `Created ${name.trim()}`).then((ok) => ok && setName("")); }}>
          <Field label="Create another team"><div className="flex gap-2"><Input value={name} onChange={(e) => setName(e.target.value)} /><Button type="submit" disabled={!name.trim()}>Create</Button></div></Field>
        </form>
      </Card>
    </Section>
  );
}
