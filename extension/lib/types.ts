export type Model = 'deepseek-flash' | 'deepseek-v4-pro';

export interface Settings {
  deepseekKey: string;
  model: Model;
  myUsername: string; // excluded from lead finding
  // Governor
  dailyDmCap: number; // hard ceiling per day
  warmup: boolean; // ramp the cap up over the first days
  warmupStart: number; // epoch ms when warm-up began
  activeStart: number; // hour 0-23 (local)
  activeEnd: number; // hour 0-23 (local)
  minGapSec: number;
  maxGapSec: number;
  pace: number; // overall speed of on-page actions: 0.5 (slow) .. 2 (fast), 1 = normal
  // Brain
  minFit: number; // 0-100 threshold for a person to become a lead
  sessionMinutes: number;
  // Engagement
  upvotes: boolean;
  maxUpvotesPerSession: number;
  // DMs
  dmMode: 'template' | 'ai';
  aiGuidance: string; // extra instructions appended to the DM-writer prompt
  autoSchedule: boolean; // run sessions on a schedule inside active hours
  sessionsPerDay: number;
}

export interface Product {
  url: string;
  name: string;
  summary: string;
  offer: string;
  icp: string;
  personas: string[];
  painPhrases: string[];
  buyingSignals: string[];
  competitors: string[];
  subredditIdeas: string[];
  searchQueries: string[];
}

export type RulesRisk = 'low' | 'medium' | 'high';

export interface Subreddit {
  name: string; // without r/
  subscribers: number;
  postsPerDay: number;
  icpDensity: number; // 0-100, from LLM over a real sample of titles
  rulesRisk: RulesRisk;
  score: number; // 0-100 composite
  reason: string;
  enabled: boolean;
  lastVisited?: number;
}

export type LeadStatus =
  | 'queued' // found, no message yet
  | 'draft' // message written, waiting for approval
  | 'ready' // approved / will be sent when governor allows
  | 'sent'
  | 'replied'
  | 'skipped'
  | 'failed';

export interface Lead {
  username: string;
  subreddit: string;
  postUrl: string;
  postTitle: string;
  snippet: string; // what they wrote that made them a fit
  fit: number;
  intent: string;
  reason: string;
  status: LeadStatus;
  message?: string;
  error?: string;
  source: 'browse' | 'page' | 'csv';
  createdAt: number;
  sentAt?: number;
}

export interface Template {
  id: string;
  name: string;
  body: string;
}

export interface Opportunity {
  id: string; // post permalink
  postUrl: string;
  title: string;
  subreddit: string;
  author: string;
  draft: string;
  why: string;
  status: 'pending' | 'posted' | 'skipped' | 'failed';
  createdAt: number;
}

export interface PostIdea {
  id: string;
  subreddit: string;
  title: string;
  body: string;
  angle: string;
}

export interface Stats {
  scanned: number;
  leads: number;
  upvotes: number;
  dms: number;
  errors: number;
}

export interface AgentState {
  running: boolean;
  mode: RunMode;
  phase: string;
  subreddit: string;
  startedAt: number;
  stats: Stats;
  lastError?: string; // why the last session ended early, shown on the run card
}

export type RunMode = 'full' | 'find' | 'dm' | 'warmup';

export interface LogLine {
  t: number;
  level: 'info' | 'warn' | 'error' | 'ok';
  msg: string;
}

export interface Daily {
  date: string;
  count: number;
  sessions: number;
  nextDmAt: number;
  pausedUntil: number;
  pauseReason?: string; // why DMs were paused (rate limit, captcha, repeated failures)
}

// ---- content-script protocol ----
export interface PostData {
  id: string;
  title: string;
  author: string;
  subreddit: string;
  permalink: string;
  score: number;
  comments: number;
  body: string;
}

export interface CommentData {
  id: string;
  author: string;
  permalink: string;
  depth: number;
  score: number;
  text: string;
}

export interface DmResult {
  ok: boolean;
  code?:
    | 'no-chat-button'
    | 'no-composer'
    | 'send-failed'
    | 'rate-limit'
    | 'blocked'
    | 'captcha'
    | 'unknown';
  error?: string;
}

// ---- SOP-driven warm-up ----
/** Everything Pigeon can actually do on Reddit. The SOP compiler may only schedule these; anything else becomes a manual step. */
export type WarmupAction = 'browse' | 'upvote' | 'comment' | 'post' | 'join' | 'dm';

/** Per-day ceilings for one phase. browse is minutes of reading; the rest are counts. */
export interface WarmupQuota {
  browse: number;
  upvote: number;
  comment: number;
  post: number;
  join: number;
  dm: number;
}

export interface WarmupPhase {
  name: string;
  fromDay: number; // account day, 1-based, inclusive
  toDay: number; // inclusive
  goal: string;
  quota: WarmupQuota;
  minKarma?: number; // stay in the earlier phase until the account has this much karma
  notes: string;
}

/** Subreddit-specific behaviour straight from the SOP (e.g. "only read r/SaaS until day 5, then comment"). */
export interface WarmupSub {
  name: string;
  fromDay: number;
  actions: WarmupAction[]; // what may be done there (always intersected with the phase quota)
  notes: string;
}

export interface WarmupPlan {
  name: string;
  summary: string;
  phases: WarmupPhase[];
  subreddits: WarmupSub[];
  voice: string; // how comments/posts should read
  rules: string[]; // hard rules for everything written
  avoid: string[];
  sessionsPerDay: number;
  manualSteps: string[]; // SOP steps Pigeon can't do itself
  assumptions: string[]; // things the compiler had to guess: shown so the user can fix them
  warnings: string[]; // e.g. quotas lowered to Pigeon's safety ceiling
  builtAt: number;
}

export interface SopDoc {
  id: string;
  name: string;
  via: string; // how the text was read: text, pdf, pdf-ocr, image-ocr, docx, ...
  chars: number;
  text: string;
  addedAt: number;
  note?: string; // e.g. "pages 13-40 skipped"
}

export interface WarmupState {
  enabled: boolean; // scheduled sessions run the warm-up while the plan is in progress
  startedAt: number; // epoch ms of account day 1
  docs: SopDoc[];
  plan: WarmupPlan | null;
  approval: 'review' | 'auto'; // review: drafted comments/posts wait for a click
}

export type WarmupCounts = WarmupQuota;

export interface WarmupDaily {
  date: string;
  done: WarmupCounts;
  targets: WarmupCounts; // today's randomised goal (60-100% of the ceiling), fixed for the day
  phase: string;
  karma: number | null; // account karma when the day started; null = couldn't be read
}

export interface WarmupItem {
  id: string;
  kind: 'comment' | 'post';
  subreddit: string;
  postUrl?: string; // comment: the thread to reply in
  postTitle?: string;
  title?: string; // post: its title
  body: string;
  why: string;
  status: 'pending' | 'posted' | 'skipped' | 'failed';
  error?: string;
  createdAt: number;
}
