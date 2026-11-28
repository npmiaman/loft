"use client";
import { Eye, EyeOff } from "lucide-react";
import { motion } from "motion/react";
import * as React from "react";
import { api } from "@/lib/api";
import { Button } from "./ui/button";
import { Field, Input } from "./ui/field";
import { AuthPage } from "./ui/auth-page";

type Mode = "in" | "up";
type Status = { reachable: boolean; accounts?: boolean | null; error?: string; server?: string };
const EXISTS = "That email already has an account.";

function PasswordInput({ value, onChange, autoComplete }: { value: string; onChange: (v: string) => void; autoComplete: string }) {
  const [shown, setShown] = React.useState(false);
  return (
    <div className="relative">
      <Input id="pw" type={shown ? "text" : "password"} autoComplete={autoComplete} value={value} onChange={(e) => onChange(e.target.value)} className="pr-10" />
      <button type="button" onClick={() => setShown(!shown)} aria-label={shown ? "Hide password" : "Show password"}
        className="absolute inset-y-0 right-0 grid w-10 place-items-center text-mute hover:text-ink">
        {shown ? <EyeOff className="size-4" aria-hidden /> : <Eye className="size-4" aria-hidden />}
      </button>
    </div>
  );
}

/**
 * Sign in and create account are separate modes. A brand-new server (no accounts yet) opens on "Create the first account", which
 * needs no invite. It always uses the app's saved server.
 */
export function Login({ server, onDone }: { server?: string; onDone: () => Promise<void> }) {
  const [f, setF] = React.useState({ email: "", password: "", name: "", invite: "" });
  const [mode, setMode] = React.useState<Mode | null>(null);
  const [status, setStatus] = React.useState<Status | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState("");

  // Is the app's server new (no accounts yet)? Then open on "create the first account".
  React.useEffect(() => {
    let alive = true;
    api<Status>("GET", "/auth/status").catch(() => ({ reachable: false } as Status)).then((s) => {
      if (!alive) return;
      setStatus(s);
      setMode((m) => (s.reachable && s.accounts === false ? "up" : m ?? "in"));
    });
    return () => { alive = false; };
  }, []);
  React.useEffect(() => { const t = setTimeout(() => setMode((m) => m ?? "in"), 3000); return () => clearTimeout(t); }, []); // never stuck on a spinner

  const first = !!status?.reachable && status.accounts === false;
  const up = mode === "up";
  const set = (k: keyof typeof f) => (v: string) => { setF((x) => ({ ...x, [k]: v })); setErr(""); };
  const ev = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => set(k)(e.target.value);
  const switchTo = (m: Mode) => { setMode(m); setErr(""); };

  async function submit() {
    setErr("");
    const email = f.email.trim();
    if (!email) return setErr("Enter your email.");
    if (!/^\S+@\S+\.\S+$/.test(email)) return setErr("That doesn't look like an email address.");
    if (!f.password) return setErr(up ? "Choose a password." : "Enter your password.");
    if (up && f.password.length < 8) return setErr("Choose a password of at least 8 characters.");
    setBusy(true);
    try {
      await api("POST", "/login", { server: "", email, password: f.password, signup: up, name: up ? f.name.trim() : "",
        invite: up && !first ? f.invite.trim() || null : null });
      await onDone();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }

  if (!mode) {
    return <AuthPage><div className="grid place-items-center py-20"><i className="size-5 animate-spin rounded-full border-2 border-line2 border-t-ink" aria-label="Loading" /></div></AuthPage>;
  }

  const heading = !up ? "Welcome back" : first ? "Create the first account" : "Create your account";
  const sub = !up ? "Sign in to your team's browsers." : first ? "This server is new. You'll be its owner, and can invite your team next."
    : "Join your team with the invite code a teammate sent you.";

  return (
    <AuthPage>
      <motion.form key={mode} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25, ease: "easeOut" }}
        noValidate onSubmit={(e) => { e.preventDefault(); void submit(); }} className="space-y-4">
        <div className="flex flex-col space-y-1">
          <h1 className="text-2xl font-bold tracking-tight">{heading}</h1>
          <p className="text-base text-mute">{sub}</p>
        </div>

        <div className="space-y-4">
          {up && <Field label="Your name" hint="Optional. Your team sees it next to your email."><Input id="nm" autoComplete="name" value={f.name} onChange={ev("name")} /></Field>}
          <Field label="Email"><Input id="em" type="email" autoComplete={up ? "email" : "username"} value={f.email} onChange={ev("email")} autoFocus /></Field>
          <Field label="Password" hint={up ? "At least 8 characters." : undefined}>
            <PasswordInput value={f.password} onChange={set("password")} autoComplete={up ? "new-password" : "current-password"} />
          </Field>
          {up && !first && <Field label="Invite code" hint="From the teammate who invited you."><Input id="inv" value={f.invite} onChange={ev("invite")} autoComplete="off" /></Field>}
        </div>

        {err && (
          <div className="space-y-2">
            <p role="alert" className="rounded-md bg-bad-bg px-3 py-2 text-sm text-bad">{err}</p>
            {err === EXISTS && <Button type="button" size="sm" onClick={() => switchTo("in")}>Sign in instead</Button>}
          </div>
        )}

        <Button type="submit" variant="primary" className="w-full" loading={busy} disabled={busy}>{up ? "Create account" : "Sign in"}</Button>

        {!first && (
          <p className="text-sm text-mute">
            {up ? "Already have an account? " : "New here? "}
            <button type="button" onClick={() => switchTo(up ? "in" : "up")} className="font-medium text-ink underline-offset-4 hover:underline">
              {up ? "Sign in" : "Create an account"}
            </button>
          </p>
        )}

      </motion.form>
    </AuthPage>
  );
}
