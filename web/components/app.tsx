"use client";
import * as React from "react";
import { api } from "@/lib/api";
import { LoftProvider } from "@/lib/store";
import { AccountsPage } from "./accounts/accounts-page";
import { Login } from "./login";
import { ActivityPage } from "./pages/activity";
import { OverviewPage } from "./pages/overview";
import { TeamPage } from "./pages/team";
import { Shell, type Tab } from "./shell";
import { Button } from "./ui/button";
import { Field, Input } from "./ui/field";
import { Callout } from "./ui/bits";
import { useLoft } from "@/lib/store";

function NoTeam() {
  const { act } = useLoft(); const [name, setName] = React.useState(""); const [code, setCode] = React.useState("");
  return (
    <div className="mx-auto mt-24 w-full max-w-md space-y-6 rounded-xl border border-line p-6">
      <div><h1 className="text-xl font-semibold tracking-tight">You're not in a team yet</h1><p className="text-mute">Create one, or join with an invite code.</p></div>
      <Field label="Create a team"><div className="flex gap-2"><Input id="tn" value={name} onChange={(e) => setName(e.target.value)} placeholder="Team name" /><Button variant="primary" disabled={!name.trim()} onClick={() => act(() => api("POST", "/teams", { name }))}>Create</Button></div></Field>
      <Field label="Or join with an invite code"><div className="flex gap-2"><Input value={code} onChange={(e) => setCode(e.target.value)} /><Button disabled={!code.trim()} onClick={() => act(() => api("POST", "/join", { code }))}>Join</Button></div></Field>
    </div>
  );
}

function Signed() {
  const { s } = useLoft();
  const [tab, setTabState] = React.useState<Tab>("accounts");
  React.useEffect(() => { const h = window.location.hash.slice(1) as Tab; if (["accounts", "team", "activity", "overview"].includes(h)) setTabState(h); }, []);
  const setTab = (t: Tab) => { setTabState(t); window.history.replaceState(null, "", "#" + t); };
  const noTeam = !s.me?.teams.length;
  return (
    <Shell tab={tab} setTab={setTab}>
      {noTeam ? <NoTeam /> : !s.has_key ? (
        <Callout tone="warn"><b>This device doesn't have the team's encryption key.</b> <span className="text-mute">Accounts are end-to-end encrypted. Join with the full invite code (it ends with <code>.key</code>), or sign in again with your password.</span></Callout>
      ) : tab === "accounts" ? <AccountsPage /> : tab === "team" ? <TeamPage /> : tab === "activity" ? <ActivityPage /> : <OverviewPage />}
    </Shell>
  );
}

export function App() {
  return (
    <LoftProvider>
      {(s, error, refresh) => !s
        ? <div className="grid h-screen place-items-center">{error
            ? <p className="max-w-sm text-center text-bad">Can't reach the Loft app: {error}</p>
            : <i className="size-5 animate-spin rounded-full border-2 border-line2 border-t-ink" aria-label="Loading" />}</div>
        : !s.signed_in
          ? <Login server={s.server} onDone={refresh} />
          : <Signed />}
    </LoftProvider>
  );
}
