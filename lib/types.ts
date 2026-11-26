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
  approveAiDrafts: boolean; // AI drafts wait for approval before sending
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
}

export type RunMode = 'full' | 'find' | 'dm';

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
