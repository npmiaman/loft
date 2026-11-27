"use client";
import { Check, Download, Plus, Search } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import * as React from "react";
import { api } from "@/lib/api";
import { useLoft } from "@/lib/store";
import type { Profile } from "@/lib/types";
import { Callout, Card, PageHead } from "../ui/bits";
import { Button } from "../ui/button";
import { PigeonDrawer } from "../pigeon/drawer";
import { AccountRow } from "./account-row";
import { AddAccount, AddMany, Cookies, EditAccount, ShareAccount } from "./dialogs";

function Steps({ has, opened, piged }: { has: boolean; opened: boolean; piged: boolean }) {
  const step = (n: number, done: boolean, title: string, text: string) => (
    <li key={n} className="flex gap-3">
      <span className={`grid size-6 shrink-0 place-items-center rounded-full border text-xs font-semibold ${done ? "border-ok bg-ok text-white" : "border-line2 text-mute"}`}>
        {done ? <motion.span initial={{ scale: 0 }} animate={{ scale: 1 }} transition={{ type: "spring", stiffness: 500, damping: 20 }}><Check className="size-3.5" /></motion.span> : n}
      </span>
      <div><div className={`font-semibold ${done ? "text-mute line-through decoration-line2" : ""}`}>{title}</div><div className="text-[13px] text-mute">{text}</div></div>
    </li>
  );
  return (
    <ol className="grid gap-4 md:grid-cols-3">
      {step(1, has, "Add an account", "Give it a name and its own proxy.")}
      {step(2, opened, "Open it and sign in", "Press Open browser, sign in to Reddit once, then Close & save.")}
      {step(3, piged, "Start Pigeon", "It finds people and drafts messages. You approve them before anything is sent.")}
    </ol>
  );
}

export function AccountsPage() {
  const { s, canAdmin, act } = useLoft();
  const ps = s.profiles ?? [];
  const [q, setQ] = React.useState("");
  const [dlg, setDlg] = React.useState<null | "add" | "many">(null);
  const [edit, setEdit] = React.useState<Profile | null>(null); const [share, setShare] = React.useState<Profile | null>(null); const [cookies, setCookies] = React.useState<Profile | null>(null);
  const [pg, setPg] = React.useState<number | null>(null);
  const [modes, setModes] = React.useState<Record<number, string>>({});
  const [off, setOff] = React.useState(true);
  React.useEffect(() => setOff(localStorage.getItem("loft.ob.off") === "1"), []);

  const has = ps.length > 0, opened = ps.some((p) => p.version > 0 || p.running_here);
  const piged = Object.values(s.jobs ?? {}).some((j) => j.pigeon && (j.pigeon.state !== "idle" || j.pigeon.stats.leads > 0));
  const shown = ps.filter((p) => !q || `${p.name} ${p.tags.join(" ")} ${p.notes}`.toLowerCase().includes(q.toLowerCase()));
  const fetchJob = s.jobs?.fetch;

  return (
    <>
      <PageHead title="Accounts" sub="Each account has its own browser and its own proxy."
        actions={<>
          <Button onClick={() => setDlg("many")}>Add many</Button>
          <Button variant="primary" onClick={() => setDlg("add")}><Plus className="size-4" aria-hidden /> Add account</Button>
        </>} />

      {!s.browser && (
        <div className="mb-4"><Callout tone="warn" action={<Button variant="primary" loading={!!fetchJob?.alive} onClick={() => act(() => api("POST", "/browser/install"))}><Download className="size-4" /> {fetchJob?.alive ? "Downloading…" : "Install browser"}</Button>}>
          <b>The browser isn't installed.</b> <span className="text-mute">It's a one-time ~1.3 GB download.</span>
          {fetchJob && <pre className="mt-1 max-h-24 overflow-auto font-mono text-xs text-mute">{fetchJob.log.join("\n")}</pre>}
        </Callout></div>
      )}

      {has && !off && !(has && opened && piged) && (
        <Card className="mb-5 p-5">
          <div className="mb-3 flex items-center justify-between"><b>Get started</b>
            <Button size="sm" variant="ghost" onClick={() => { localStorage.setItem("loft.ob.off", "1"); setOff(true); }}>Dismiss</Button></div>
          <Steps has={has} opened={opened} piged={piged} />
        </Card>
      )}

      {!has ? (
        <Card className="px-6 py-14 text-center">
          <h2 className="text-lg font-semibold tracking-tight">No accounts yet</h2>
          <p className="mx-auto mb-5 mt-1.5 max-w-md text-mute">An account is one Reddit login with its own browser and its own proxy. Add the first one to begin.</p>
          <Button variant="primary" onClick={() => setDlg("add")}><Plus className="size-4" /> Add your first account</Button>
          <div className="mx-auto mt-9 max-w-3xl text-left"><Steps has={false} opened={false} piged={false} /></div>
        </Card>
      ) : (
        <>
          <div className="relative mb-4 max-w-xs"><Search className="absolute left-3 top-2.5 size-4 text-mute" aria-hidden />
            <input aria-label="Search accounts" placeholder="Search accounts" value={q} onChange={(e) => setQ(e.target.value)}
              className="h-9 w-full rounded-lg border border-line2 bg-card pl-9 pr-3 placeholder:text-mute/80 focus:border-acc focus:outline-none focus:ring-[3px] focus:ring-acc/20" /></div>
          <Card>
            <AnimatePresence initial={false}>
              {shown.map((p) => (
                <AccountRow key={p.id} p={p} mode={modes[p.id] ?? "find"} setMode={(m) => setModes({ ...modes, [p.id]: m })} hasRunners={!!s.runners?.length} canAdmin={canAdmin}
                  onPigeon={() => setPg(p.id)} onEdit={() => setEdit(p)} onShare={() => setShare(p)} onCookies={() => setCookies(p)} />
              ))}
            </AnimatePresence>
            {!shown.length && <p className="px-5 py-10 text-center text-mute">No accounts match “{q}”.</p>}
          </Card>
        </>
      )}

      <AddAccount open={dlg === "add"} onClose={() => setDlg(null)} />
      <AddMany open={dlg === "many"} onClose={() => setDlg(null)} />
      <EditAccount p={edit} onClose={() => setEdit(null)} />
      <ShareAccount p={share} onClose={() => setShare(null)} />
      <Cookies p={cookies} onClose={() => setCookies(null)} />
      <PigeonDrawer id={pg} onClose={() => setPg(null)} />
    </>
  );
}
