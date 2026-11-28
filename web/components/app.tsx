"use client";
import * as React from "react";
import { api } from "@/lib/api";
import { PigeonProvider } from "@/lib/store";
import { AccountsPage } from "./accounts/accounts-page";
import { Login } from "./login";
import { AuthPage } from "./ui/auth-page";
import { OverviewPage } from "./pages/overview";
import { SECTION_IDS, SettingsDialog, type SectionId } from "./pages/settings";
import { NavCtx, Shell, type Tab } from "./shell";
import { Button } from "./ui/button";
import { Field, Input } from "./ui/field";
import { Callout } from "./ui/bits";
import { ConfirmProvider } from "./ui/confirm";
import { usePigeon } from "@/lib/store";

function NoTeam() {
  const { act } = usePigeon(); const [name, setName] = React.useState(""); const [code, setCode] = React.useState("");
  return (
    <AuthPage>
      <div className="flex flex-col space-y-1"><h1 className="text-2xl font-bold tracking-tight">You're not in a team yet</h1><p className="text-base text-mute">Create one, or join with an invite code.</p></div>
      <Field label="Create a team"><div className="flex gap-2"><Input id="tn" value={name} onChange={(e) => setName(e.target.value)} placeholder="Team name" /><Button variant="primary" disabled={!name.trim()} onClick={() => act(() => api("POST", "/teams", { name }))}>Create</Button></div></Field>
      <Field label="Or join with an invite code"><div className="flex gap-2"><Input value={code} onChange={(e) => setCode(e.target.value)} /><Button disabled={!code.trim()} onClick={() => act(() => api("POST", "/join", { code }))}>Join</Button></div></Field>
    </AuthPage>
  );
}

/** Signed in, but this device can't decrypt the team's accounts: paste the full invite code (it carries the key), or sign in again. */
function NoKey() {
  const { act } = usePigeon(); const [code, setCode] = React.useState(""); const [busy, setBusy] = React.useState(false);
  const join = async () => { setBusy(true); await act(() => api("POST", "/join", { code: code.trim() }), "Key added. Your accounts are unlocked."); setBusy(false); };
  return (
    <div className="max-w-xl space-y-4">
      <Callout tone="warn"><b>This device doesn't have the team's encryption key.</b> <span className="text-mute">Accounts are end-to-end encrypted, so they can't be opened here yet.</span></Callout>
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); join(); }}>
        <Field label="Full invite code" hint={<>The whole code from your invite, including the part after the last dot. Ask an owner or admin for a new one if you don&apos;t have it.</>}>
          <div className="flex gap-2"><Input id="keycode" value={code} onChange={(e) => setCode(e.target.value)} placeholder="Paste the invite code" autoComplete="off" spellCheck={false} />
            <Button type="submit" variant="primary" loading={busy} disabled={!code.trim()}>Join</Button></div>
        </Field>
      </form>
      <p className="text-sm text-mute">Or sign in again with your password on this device. <Button size="sm" variant="ghost" className="ml-1" onClick={() => act(() => api("POST", "/logout"))}>Sign in again</Button></p>
    </div>
  );
}

/** Shown when the app stops answering after it had loaded. Everything stays visible, but actions are disabled until it's back. */
function ConnectionLost() {
  const { offline, refresh } = usePigeon(); const [busy, setBusy] = React.useState(false);
  if (!offline) return null;
  return (
    <div role="alert" className="sticky top-14 z-20 flex flex-wrap items-center gap-3 border-b border-transparent bg-bad-bg px-4 py-2.5 text-sm text-bad md:top-0 md:px-10">
      <span className="min-w-0 flex-1"><b>Lost connection to the Pigeon app.</b> Retrying every few seconds. Actions are paused until it&apos;s back.</span>
      <Button size="sm" loading={busy} onClick={async () => { setBusy(true); await refresh(); setBusy(false); }}>Retry</Button>
    </div>
  );
}

function Signed() {
  const { s, offline } = usePigeon();
  const [tab, setTabState] = React.useState<Tab>("accounts");
  const [settings, setSettings] = React.useState<SectionId | null>(null); // Settings is a floating window over the current page
  const openSettings = (sub?: string) => {
    const sec = (SECTION_IDS.includes(sub as SectionId) ? sub : "profile") as SectionId;
    setSettings(sec);
    window.history.replaceState(null, "", `#settings/${sec}`);
  };
  React.useEffect(() => {
    const [h, sub] = window.location.hash.slice(1).split("/");
    if (h === "team") { openSettings("members"); return; } // the old Team page
    if (h === "settings") { openSettings(sub); return; }
    if (h === "activity") { setTabState("overview"); return; } // the old Activity page: the Overview opens its history panel
    if (["accounts", "overview"].includes(h)) setTabState(h as Tab);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const setTab = (t: Tab, sub?: string) => {
    if (t === "settings") return openSettings(sub);
    setTabState(t);
    window.history.replaceState(null, "", "#" + t);
  };
  const closeSettings = () => { setSettings(null); window.history.replaceState(null, "", "#" + tab); };
  const noTeam = !s.me?.teams.length;
  if (noTeam) return <NoTeam />;
  return (
    <NavCtx.Provider value={setTab}><Shell tab={tab} setTab={setTab} banner={<ConnectionLost />}><fieldset disabled={!!offline} className="contents">
      {!s.has_key ? <NoKey /> : tab === "accounts" ? <AccountsPage /> : <OverviewPage />}
    </fieldset></Shell>
    <SettingsDialog section={settings} onSection={(x) => { setSettings(x); window.history.replaceState(null, "", `#settings/${x}`); }} onClose={closeSettings} /></NavCtx.Provider>
  );
}

export function App() {
  return (
    <PigeonProvider>
      {(s, error, refresh) => <ConfirmProvider>{!s
        ? <div className="grid h-screen place-items-center">{error
            ? <p className="max-w-sm text-center text-bad">Can't reach the Pigeon app: {error}</p>
            : <i className="size-5 animate-spin rounded-full border-2 border-line2 border-t-ink" aria-label="Loading" />}</div>
        : !s.signed_in
          ? <Login server={s.server} onDone={refresh} />
          : <Signed />}</ConfirmProvider>}
    </PigeonProvider>
  );
}
