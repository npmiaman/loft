export type Team = { id: number; name: string; role: "owner" | "admin" | "member" };
export type Profile = {
  id: number; name: string; os: string; canvas: string; tags: string[]; notes: string; version: number;
  access: "edit" | "run"; start_url?: string;
  proxy: { server: string; username?: string } | null;
  running: { by: string; device: string } | null;
  running_here: boolean;
};
export type PigeonStatus = {
  state: "idle" | "running" | "stopping"; job: string | null; mode: string | null; since: number | null;
  last: { kind: string; ok: boolean; error?: string; out?: string } | null; error: string | null;
  central: boolean; product: string | null;
  stats: { scanned: number; leads: number; upvotes: number; dms: number; errors: number; posts?: number };
  daily: { count: number; date: string; next_dm_at: number; paused_until: number };
  leads: Record<string, number>; log: { t: number; level: string; msg: string }[];
};
export type Job = { alive: boolean; log: string[]; pigeon: PigeonStatus | null };
export type LoftState = {
  signed_in: boolean; server?: string; browser: boolean; host_os: string; jobs: Record<string, Job>;
  me?: { id: number; email: string; name?: string; teams: Team[] }; device?: string; team?: number; role?: Team["role"];
  has_key?: boolean; profiles?: Profile[]; members?: { id: number; name: string; email: string; role: string }[];
  templates?: { id: number; name: string; settings: Record<string, unknown> }[]; runners?: { id: number; name: string; url: string }[];
};
export type Lead = { username: string; fit: number; status: string; subreddit?: string; reason: string; message?: string; error?: string };
export type Overview = {
  profiles: number; members: number; members_without_key: string[]; running: { name: string; email: string; device: string }[];
  bytes: number; versions: number; open_invites: number; key_epoch: number; rotating: boolean; last_rotation: number | null;
  recent: { at: number; email: string | null; action: string; detail: string }[];
};
