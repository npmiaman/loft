import { contactedAfterClear, migrateSettings, unsentLeadKeys } from './safety';
import type {
  AgentState, Daily, Lead, LogLine, Opportunity, PostIdea, Product, Settings, Subreddit, Template,
  WarmupDaily, WarmupItem, WarmupState,
} from './types';

export const DEFAULT_TEMPLATE: Template = {
  id: 'default',
  name: 'Default',
  body:
    "{Hey|Hi|Hello} {name}, saw your post in r/{subreddit} about {topic}. " +
    "I'm building something that helps with exactly that — {happy to share|would love your take} if useful?",
};

export const DEFAULT_SETTINGS: Settings = {
  deepseekKey: '',
  model: 'deepseek-flash',
  myUsername: '',
  dailyDmCap: 40,
  warmup: true,
  warmupStart: 0,
  activeStart: 9,
  activeEnd: 21,
  minGapSec: 60,
  maxGapSec: 180,
  pace: 1,
  minFit: 70,
  sessionMinutes: 30,
  upvotes: true,
  maxUpvotesPerSession: 10,
  dmMode: 'ai',
  aiGuidance: '',
  autoSchedule: false,
  sessionsPerDay: 3,
};

export const EMPTY_AGENT: AgentState = {
  running: false,
  mode: 'full',
  phase: 'idle',
  subreddit: '',
  startedAt: 0,
  stats: { scanned: 0, leads: 0, upvotes: 0, dms: 0, errors: 0 },
};

export const EMPTY_WARMUP: WarmupState = { enabled: false, startedAt: 0, docs: [], plan: null, approval: 'review' };

export interface KV {
  settings: Settings;
  product: Product | null;
  subreddits: Subreddit[];
  templates: Template[];
  opportunities: Opportunity[];
  postIdeas: PostIdea[];
  log: LogLine[];
  daily: Daily;
  agent: AgentState;
  warmup: WarmupState;
  warmupJoined: string[]; // subreddits the warm-up already joined, lowercase (written by the background only)
  warmupDaily: WarmupDaily | null;
  warmupItems: WarmupItem[];
  onboarded: boolean; // first-run checklist finished
  contacted: string[]; // lowercase usernames already DMed. Clearing leads never removes anyone from it.
}

const today = () => new Date().toISOString().slice(0, 10);

const DEFAULTS: KV = {
  settings: DEFAULT_SETTINGS,
  product: null,
  subreddits: [],
  templates: [DEFAULT_TEMPLATE],
  opportunities: [],
  postIdeas: [],
  log: [],
  daily: { date: today(), count: 0, sessions: 0, nextDmAt: 0, pausedUntil: 0 },
  agent: EMPTY_AGENT,
  warmup: EMPTY_WARMUP,
  warmupJoined: [],
  warmupDaily: null,
  warmupItems: [],
  onboarded: false,
  contacted: [],
};

export async function get<K extends keyof KV>(key: K): Promise<KV[K]> {
  const r = await chrome.storage.local.get(key);
  return (r[key] ?? DEFAULTS[key]) as KV[K];
}

export async function set<K extends keyof KV>(key: K, value: KV[K]): Promise<void> {
  await chrome.storage.local.set({ [key]: value });
}

export async function getSettings(): Promise<Settings> {
  return migrateSettings(await get('settings'));
}

export async function getDaily(): Promise<Daily> {
  const d = await get('daily');
  if (d.date !== today()) {
    const fresh = { ...DEFAULTS.daily, date: today(), pausedUntil: d.pausedUntil, nextDmAt: d.nextDmAt };
    await set('daily', fresh);
    return fresh;
  }
  return d;
}

export async function patchDaily(patch: Partial<Daily>): Promise<Daily> {
  const d = { ...(await getDaily()), ...patch };
  await set('daily', d);
  return d;
}

// ---- leads: one key per lead so concurrent writers never clobber each other ----
const LEAD = 'lead:';
export const leadKey = (u: string) => LEAD + u.toLowerCase();

export async function getLead(username: string): Promise<Lead | undefined> {
  const r = await chrome.storage.local.get(leadKey(username));
  return r[leadKey(username)] as Lead | undefined;
}

export async function putLead(lead: Lead): Promise<void> {
  await chrome.storage.local.set({ [leadKey(lead.username)]: lead });
}

export async function patchLead(username: string, patch: Partial<Lead>): Promise<void> {
  const cur = await getLead(username);
  if (cur) await putLead({ ...cur, ...patch });
}

export async function listLeads(): Promise<Lead[]> {
  const all = await chrome.storage.local.get(null);
  return Object.entries(all)
    .filter(([k]) => k.startsWith(LEAD))
    .map(([, v]) => v as Lead)
    .sort((a, b) => b.fit - a.fit || b.createdAt - a.createdAt);
}

/** Everyone already messaged: the persistent set plus any lead marked sent/replied (covers data from before the set existed). */
export async function getContacted(): Promise<Set<string>> {
  return contactedAfterClear(await get('contacted'), await listLeads());
}

/** True if this person is already a lead or was messaged before (even if their lead was cleared since). */
export async function isKnown(username: string): Promise<boolean> {
  return !!(await getLead(username)) || (await getContacted()).has(username.toLowerCase());
}

export async function markContacted(username: string): Promise<void> {
  const cur = await get('contacted');
  if (!cur.includes(username.toLowerCase())) await set('contacted', [...cur, username.toLowerCase()]);
}

/** Remove leads. `unsentOnly` keeps sent/replied leads. Either way, everyone already messaged stays in the contacted set. */
export async function clearLeads(unsentOnly = false): Promise<void> {
  const leads = await listLeads();
  await set('contacted', [...contactedAfterClear(await get('contacted'), leads)]);
  await chrome.storage.local.remove(unsentOnly ? unsentLeadKeys(leads) : leads.map((l) => leadKey(l.username)));
}

// ---- log (written by background only) ----
let logQueue: Promise<unknown> = Promise.resolve();
export function log(level: LogLine['level'], msg: string): void {
  logQueue = logQueue.then(async () => {
    const cur = await get('log');
    cur.push({ t: Date.now(), level, msg });
    await set('log', cur.slice(-300));
  });
}
