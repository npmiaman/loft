"use client";
import { motion } from "motion/react";
import * as React from "react";
import { api } from "@/lib/api";
import { Button } from "./ui/button";
import { Field, Input, Textarea } from "./ui/field";
import { ShaderBg } from "./shader-bg";

export function Login({ server, onDone }: { server?: string; onDone: () => Promise<void> }) {
  const [f, setF] = React.useState({ server: server || "http://127.0.0.1:4400", email: "", password: "", name: "", invite: "", ca: "" });
  const [more, setMore] = React.useState(false);
  const [busy, setBusy] = React.useState<"in" | "up" | null>(null);
  const [err, setErr] = React.useState("");
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setF({ ...f, [k]: e.target.value });

  async function go(signup: boolean) {
    setBusy(signup ? "up" : "in"); setErr("");
    try {
      await api("POST", "/login", { server: f.server.trim(), email: f.email.trim(), password: f.password, signup, name: f.name, invite: f.invite.trim() || null, ca: f.ca || null });
      await onDone();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(null); }
  }

  return (
    <div className="grid min-h-screen lg:grid-cols-[minmax(420px,520px)_1fr]">
      <div className="flex flex-col justify-center px-8 py-12 sm:px-16">
        <motion.form
          initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35, ease: "easeOut" }}
          onSubmit={(e) => { e.preventDefault(); go(false); }} className="mx-auto w-full max-w-sm"
        >
          <div className="mb-8 flex items-center gap-2.5 text-[15px] font-semibold">
            <span className="grid size-[26px] place-items-center rounded-lg bg-ink text-sm font-bold text-on-ink">P</span> Pigeon
          </div>
          <h1 className="text-[26px] font-semibold tracking-[-0.025em]">Welcome back</h1>
          <p className="mb-6 mt-1 text-mute">Sign in to your team's browsers.</p>
          <div className="space-y-4">
            <Field label="Server"><Input id="srv" value={f.server} onChange={set("server")} /></Field>
            <Field label="Email"><Input id="em" type="email" autoComplete="username" value={f.email} onChange={set("email")} autoFocus /></Field>
            <Field label="Password"><Input id="pw" type="password" autoComplete="current-password" value={f.password} onChange={set("password")} /></Field>
          </div>
          <button type="button" onClick={() => setMore(!more)} className="mt-4 text-[13px] text-mute hover:text-ink">
            {more ? "Hide" : "New here, or using a self-signed server?"}
          </button>
          {more && (
            <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} className="mt-3 space-y-4 overflow-hidden">
              <Field label="Your name (new account)"><Input value={f.name} onChange={set("name")} /></Field>
              <Field label="Invite code (needed except for the first user)"><Input value={f.invite} onChange={set("invite")} /></Field>
              <Field label="Server certificate (self-signed only)"><Textarea value={f.ca} onChange={set("ca")} placeholder="-----BEGIN CERTIFICATE-----" /></Field>
            </motion.div>
          )}
          {err && <p role="alert" className="mt-4 rounded-lg bg-bad-bg px-3 py-2 text-[13px] text-bad">{err}</p>}
          <div className="mt-6 flex gap-2">
            <Button type="submit" variant="primary" className="flex-1" loading={busy === "in"} disabled={!!busy}>Sign in</Button>
            <Button type="button" className="flex-1" loading={busy === "up"} disabled={!!busy} onClick={() => go(true)}>Create account</Button>
          </div>
          <p className="mt-6 text-xs text-mute">Your cookies and storage are end-to-end encrypted. The server only ever stores ciphertext.</p>
        </motion.form>
      </div>
      <div className="relative hidden overflow-hidden lg:block">
        <ShaderBg className="absolute inset-0 size-full" />
        <div className="absolute inset-x-0 bottom-0 p-12 text-white">
          <p className="max-w-md text-2xl font-medium leading-snug tracking-tight">One browser per account. One proxy per browser. One shared memory for the whole team.</p>
        </div>
      </div>
    </div>
  );
}
