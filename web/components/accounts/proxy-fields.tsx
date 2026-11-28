"use client";
import { CheckCircle2, Eye, EyeOff, XCircle } from "lucide-react";
import * as React from "react";
import { api } from "@/lib/api";
import { Button } from "../ui/button";
import { Field, Input, Select } from "../ui/field";

export type ProxyForm = { type: "http" | "https" | "socks5"; host: string; port: string; user: string; pass: string; name: string };
export const emptyProxy = (): ProxyForm => ({ type: "http", host: "", port: "", user: "", pass: "", name: "" });

/** "socks5://us%40er:pw@1.2.3.4:1080" -> fields (credentials decoded). null if it isn't a proxy URL. */
export function parseProxyUrl(raw: string): Omit<ProxyForm, "name"> | null {
  const s = raw.trim();
  if (!s) return null;
  try {
    const u = new URL(s.includes("://") ? s : `http://${s}`);
    const type = u.protocol.replace(":", "") as ProxyForm["type"];
    if (!["http", "https", "socks5"].includes(type) || !u.hostname) return null;
    return { type, host: u.hostname.replace(/^\[|\]$/g, ""), port: u.port, user: decodeURIComponent(u.username), pass: decodeURIComponent(u.password) };
  } catch { return null; }
}

/** Fields -> the URL the app stores (credentials encoded, so an @ or : in a password is safe). "" when there's no proxy. */
export function proxyUrl(f: ProxyForm): string {
  if (!f.host.trim()) return "";
  const host = f.host.trim().includes(":") ? `[${f.host.trim()}]` : f.host.trim(); // IPv6
  const auth = f.user ? `${encodeURIComponent(f.user)}:${encodeURIComponent(f.pass)}@` : "";
  return `${f.type}://${auth}${host}:${f.port.trim()}`;
}

/** What's wrong with these settings, or "" if they're fine (an empty host means "no proxy"). */
export function proxyProblem(f: ProxyForm): string {
  if (!f.host.trim()) return "";
  const p = Number(f.port);
  if (!f.port.trim() || !Number.isInteger(p) || p < 1 || p > 65535) return "Enter the proxy's port (a number from 1 to 65535).";
  return "";
}

type Tested = { ok: boolean; ip?: string; ms?: number; error?: string; warning?: string };

/** Proxy settings as separate fields, with a name for the proxy and a Test proxy button. */
export function ProxyFields({ value, onChange, disabled }: { value: ProxyForm; onChange: (f: ProxyForm) => void; disabled?: boolean }) {
  const [shown, setShown] = React.useState(false);
  const [testing, setTesting] = React.useState(false);
  const [tested, setTested] = React.useState<Tested | null>(null);
  const set = (k: keyof ProxyForm) => (v: string) => { onChange({ ...value, [k]: v }); setTested(null); };
  const onHost = (v: string) => {
    const parsed = /:\/\/|@/.test(v) ? parseProxyUrl(v) : null; // a whole proxy URL pasted into Host fills every field
    if (parsed) onChange({ ...value, ...parsed }); else set("host")(v);
    setTested(null);
  };
  const problem = proxyProblem(value);
  const test = async () => {
    setTesting(true); setTested(null);
    try { setTested(await api<Tested>("POST", "/proxy/test", { proxy: proxyUrl(value) })); }
    catch (e) { setTested({ ok: false, error: (e as Error).message }); }
    finally { setTesting(false); }
  };
  return (
    <fieldset disabled={disabled} className="space-y-3">
      <div className="grid grid-cols-[110px_1fr_92px] gap-2">
        <Field label="Type"><Select id="px_type" value={value.type} onChange={(e) => set("type")(e.target.value)}>
          <option value="http">HTTP</option><option value="https">HTTPS</option><option value="socks5">SOCKS5</option>
        </Select></Field>
        <Field label="Host"><Input id="px_host" value={value.host} onChange={(e) => onHost(e.target.value)} placeholder="1.2.3.4 or proxy.example.com" autoComplete="off" spellCheck={false} /></Field>
        <Field label="Port"><Input id="px_port" value={value.port} onChange={(e) => set("port")(e.target.value.replace(/\D/g, ""))} placeholder="8080" inputMode="numeric" /></Field>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Username"><Input id="px_user" value={value.user} onChange={(e) => set("user")(e.target.value)} placeholder="Optional" autoComplete="off" spellCheck={false} /></Field>
        <Field label="Password">
          <div className="relative">
            <Input id="px_pass" type={shown ? "text" : "password"} value={value.pass} onChange={(e) => set("pass")(e.target.value)} placeholder="Optional" autoComplete="new-password" className="pr-10" />
            <button type="button" onClick={() => setShown(!shown)} aria-label={shown ? "Hide proxy password" : "Show proxy password"} className="absolute inset-y-0 right-0 grid w-10 place-items-center text-mute hover:text-ink">
              {shown ? <EyeOff className="size-4" aria-hidden /> : <Eye className="size-4" aria-hidden />}
            </button>
          </div>
        </Field>
      </div>
      <Field label="Name" hint="Shown in the account list instead of the address, like the provider or country.">
        <Input id="px_name" value={value.name} maxLength={60} onChange={(e) => set("name")(e.target.value)} placeholder="US residential" />
      </Field>
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" size="sm" loading={testing} disabled={!value.host.trim() || !!problem} onClick={test}>Test proxy</Button>
        {problem && value.host.trim() && <span className="text-xs text-bad">{problem}</span>}
        {tested && (tested.ok
          ? <span className="inline-flex items-center gap-1.5 text-sm text-ok"><CheckCircle2 className="size-4" aria-hidden /> Working · exit IP {tested.ip}{tested.ms ? ` · ${tested.ms} ms` : ""}{tested.warning ? <span className="text-warn"> · {tested.warning}</span> : null}</span>
          : <span className="inline-flex items-center gap-1.5 text-sm text-bad"><XCircle className="size-4" aria-hidden /> {tested.error}</span>)}
      </div>
    </fieldset>
  );
}
