"use client";
import * as D from "@radix-ui/react-dialog";
import { Building2, Cloud, LayoutTemplate, Laptop, LogOut, Moon, Palette, Pencil, Sun, Terminal, UserRound, Users, X } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { usePigeon } from "@/lib/store";
import { getTheme, setTheme, type Theme } from "@/lib/theme";
import { Callout } from "../ui/bits";
import { Button, IconButton } from "../ui/button";
import { Input } from "../ui/field";
import { SettingsGroup, SettingsNav, SettingsNavItem, SettingsNavSeparator, SettingsPane, SettingsPanel, SettingsRow, SettingsSegmented } from "../ui/settings-panel";
import { Automation, InPane, Invites, Members, OtherTeams, Runners, Templates } from "./team";

export type SectionId = "profile" | "appearance" | "members" | "templates" | "runners" | "automation" | "teams";
type Def = { id: SectionId; label: string; group: string; icon: typeof UserRound; description: string; admin?: boolean };

const SECTIONS: Def[] = [
  { id: "profile", label: "Personal", group: "you", icon: UserRound, description: "Who you're signed in as, on this computer." },
  { id: "appearance", label: "Appearance", group: "you", icon: Palette, description: "Saved on this computer." },
  { id: "members", label: "Members", group: "team", icon: Users, description: "Who's in this team and what they can do." },
  { id: "templates", label: "Templates", group: "team", icon: LayoutTemplate, description: "Starting settings for new accounts. Stored encrypted." },
  { id: "runners", label: "Cloud runners", group: "team", icon: Cloud, description: "Servers that run accounts for you, viewable from any browser or phone." },
  { id: "automation", label: "Automation API", group: "computer", icon: Terminal, description: "Scripts on this computer can manage accounts through a local API." },
  { id: "teams", label: "Teams", group: "teams", icon: Building2, description: "Join another team with an invite code, or start a new one. Switch teams from the menu at the top of the sidebar." },
];
export const SECTION_IDS = SECTIONS.map((x) => x.id);

const Value = ({ children }: { children: React.ReactNode }) => <span className="max-w-[min(22rem,60vw)] truncate text-sm text-mute">{children}</span>;

/** Your display name, edited in place: Enter saves, Escape cancels. */
function NameRow() {
  const { s, refresh } = usePigeon();
  const [editing, setEditing] = React.useState(false);
  const [val, setVal] = React.useState(""); const [err, setErr] = React.useState(""); const [busy, setBusy] = React.useState(false);
  const start = () => { setVal(s.me?.name ?? ""); setErr(""); setEditing(true); };
  const save = async () => {
    const name = val.trim();
    if (!name) return setErr("Enter a name.");
    setBusy(true);
    try { await api("PATCH", "/me", { name }); await refresh(); toast.success("Name saved"); setEditing(false); }
    catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  if (!editing) {
    return (
      <SettingsRow label="Name" description="Your team sees it next to your email.">
        <Value>{s.me?.name || "Not set"}</Value>
        <IconButton icon={Pencil} label="Edit name" onClick={start} />
      </SettingsRow>
    );
  }
  return (
    <SettingsRow stacked label="Name" description="Your team sees it next to your email.">
      <form className="flex w-full flex-wrap items-start gap-2" onSubmit={(e) => { e.preventDefault(); void save(); }}>
        <div className="min-w-56 flex-1">
          <Input id="settings_name" autoFocus maxLength={80} autoComplete="name" value={val} aria-label="Name" aria-invalid={!!err}
            onChange={(e) => { setVal(e.target.value); setErr(""); }} onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setEditing(false); } }} />
          {err && <p className="mt-1.5 text-xs text-bad">{err}</p>}
        </div>
        <Button type="submit" variant="primary" loading={busy}>Save</Button>
        <Button type="button" onClick={() => setEditing(false)}>Cancel</Button>
      </form>
    </SettingsRow>
  );
}

function Profile() {
  const { s, act } = usePigeon();
  const team = s.me?.teams.find((t) => t.id === s.team);
  return (
    <>
      <SettingsGroup title="Account">
        <NameRow />
        <SettingsRow label="Email"><Value>{s.me?.email}</Value></SettingsRow>
        <SettingsRow label="Team"><Value>{team?.name ?? "None"}</Value></SettingsRow>
        <SettingsRow label="This computer"><Value>{s.device}</Value></SettingsRow>
      </SettingsGroup>
      <SettingsGroup title="Session">
        <SettingsRow label="Sign out" description="Signs Pigeon out on this computer. Open browsers stay open.">
          <Button size="sm" onClick={() => act(() => api("POST", "/logout"))}><LogOut aria-hidden /> Sign out</Button>
        </SettingsRow>
      </SettingsGroup>
    </>
  );
}

function Appearance() {
  const [t, setT] = React.useState<Theme>("system");
  React.useEffect(() => setT(getTheme()), []);
  return (
    <SettingsGroup>
      <SettingsRow label="Theme" description="System follows this computer's light or dark setting.">
        <SettingsSegmented value={t} onValueChange={(v) => { setTheme(v as Theme); setT(v as Theme); }} options={[
          { value: "system", label: "System", icon: <Laptop /> },
          { value: "light", label: "Light", icon: <Sun /> },
          { value: "dark", label: "Dark", icon: <Moon /> },
        ]} />
      </SettingsRow>
    </SettingsGroup>
  );
}

function MembersPane() {
  const { canAdmin } = usePigeon();
  return <>{canAdmin ? <Invites /> : <Callout>Only owners and admins can invite people or change roles.</Callout>}<Members /></>;
}

/** Settings in a floating window: sections on the left (a scrolling row on narrow screens), one pane at a time. */
export function SettingsDialog({ section, onSection, onClose }: { section: SectionId | null; onSection: (s: SectionId) => void; onClose: () => void }) {
  const { canAdmin } = usePigeon();
  const shown = SECTIONS.filter((x) => !x.admin || canAdmin);
  const cur = shown.some((x) => x.id === section) ? (section as SectionId) : "profile";
  const body: Record<SectionId, React.ReactNode> = {
    profile: <Profile />, appearance: <Appearance />, members: <MembersPane />,
    templates: <Templates />, runners: <Runners />, automation: <Automation />, teams: <OtherTeams />,
  };
  return (
    <D.Root open={section !== null} onOpenChange={(o) => !o && onClose()}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-40 animate-fade bg-overlay" />
        <D.Content aria-describedby={undefined}
          onEscapeKeyDown={(e) => { if ((document.activeElement as HTMLElement | null)?.id === "settings_name") e.preventDefault(); }} // Escape there cancels the edit, not the window
          onInteractOutside={(e) => { if ((e.target as Element | null)?.closest?.("[data-sonner-toaster]")) e.preventDefault(); }}
          className="fixed left-1/2 top-1/2 z-50 flex h-[min(680px,92vh)] w-[min(980px,96vw)] -translate-x-1/2 -translate-y-1/2 animate-pop overflow-hidden rounded-xl border border-line bg-card shadow-pop focus:outline-none">
          <D.Title className="sr-only">Settings</D.Title>
          <D.Close className="absolute right-3 top-3 z-10 grid size-8 place-items-center rounded-md text-mute hover:bg-hover hover:text-ink" aria-label="Close settings"><X className="size-4" aria-hidden /></D.Close>
          <SettingsPanel value={cur} onValueChange={(v) => onSection(v as SectionId)} className="w-full">
            <SettingsNav title="Settings">
              {shown.map((x, i) => (
                <React.Fragment key={x.id}>
                  {i > 0 && shown[i - 1]!.group !== x.group && <SettingsNavSeparator />}
                  <SettingsNavItem value={x.id} icon={<x.icon />}>{x.label}</SettingsNavItem>
                </React.Fragment>
              ))}
            </SettingsNav>
            {shown.map((x) => (
              <SettingsPane key={x.id} value={x.id} title={x.label} description={x.description} data-settings-section={x.id}>
                <InPane.Provider value={true}>{body[x.id]}</InPane.Provider>
              </SettingsPane>
            ))}
          </SettingsPanel>
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}
