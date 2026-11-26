// The "brain": product profile -> subreddit discovery -> lead scoring -> message writing.
import { chatJson } from './llm';
import type { Lead, PostIdea, Product, RulesRisk, Settings, Subreddit } from './types';

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const clip = (s: string, n: number) => (s.length > n ? s.slice(0, n) + '…' : s);

// ---------------------------------------------------------------- product profile
/** Runs in an extension page (needs DOMParser). Requires host permission for the site. */
export async function scrapeSite(url: string): Promise<string> {
  const res = await fetch(url, { redirect: 'follow' });
  if (!res.ok) throw new Error(`Could not fetch ${url} (${res.status})`);
  const doc = new DOMParser().parseFromString(await res.text(), 'text/html');
  doc.querySelectorAll('script,style,noscript,svg,nav,footer').forEach((n) => n.remove());
  const meta = (n: string) =>
    doc.querySelector(`meta[name="${n}"],meta[property="${n}"]`)?.getAttribute('content') ?? '';
  const heads = [...doc.querySelectorAll('h1,h2,h3')].map((h) => h.textContent?.trim()).filter(Boolean);
  const paras = [...doc.querySelectorAll('p,li')].map((p) => p.textContent?.trim()).filter((t) => t && t.length > 30);
  return clip(
    [
      `TITLE: ${doc.title}`,
      `DESCRIPTION: ${meta('description') || meta('og:description')}`,
      `HEADINGS: ${heads.slice(0, 40).join(' | ')}`,
      `TEXT: ${paras.slice(0, 80).join(' ')}`,
    ].join('\n'),
    7000,
  );
}

export async function buildProfile(url: string, siteText: string, extra: string): Promise<Product> {
  const r = await chatJson<Omit<Product, 'url'>>({
    system:
      'You are a sharp growth strategist who finds customers on Reddit. From a product website, build a profile ' +
      'that will be used to (1) find the right subreddits, (2) recognise people with a live need, (3) write DMs. ' +
      'Respond in json only. Schema: {"name":str,"summary":str (1-2 sentences, plain),"offer":str (what we give them, 1 sentence),' +
      '"icp":str (who exactly buys this and the situation they are in),"personas":[str] (3-5),' +
      '"painPhrases":[str] (8-15 things real people literally write on Reddit when they have this problem),' +
      '"buyingSignals":[str] (5-10 post patterns like "looking for alternative to X", "anyone recommend ..."),' +
      '"competitors":[str],"subredditIdeas":[str] (20-25 real subreddit names without r/, ranging from niche to broad),' +
      '"searchQueries":[str] (6-10 reddit search queries)}. Use only subreddits you are confident exist.',
    user: `WEBSITE (${url}):\n${siteText}\n\nEXTRA CONTEXT FROM THE FOUNDER:\n${extra || '(none)'}`,
    maxTokens: 3500,
    temperature: 0.4,
  });
  return {
    url,
    name: r.name ?? '',
    summary: r.summary ?? '',
    offer: r.offer ?? '',
    icp: r.icp ?? '',
    personas: r.personas ?? [],
    painPhrases: r.painPhrases ?? [],
    buyingSignals: r.buyingSignals ?? [],
    competitors: r.competitors ?? [],
    subredditIdeas: (r.subredditIdeas ?? []).map((s) => s.replace(/^\/?r\//i, '').trim()).filter(Boolean),
    searchQueries: r.searchQueries ?? [],
  };
}

export const productBrief = (p: Product) =>
  `PRODUCT: ${p.name} — ${p.summary}\nOFFER: ${p.offer}\nICP: ${p.icp}\n` +
  `PERSONAS: ${p.personas.join('; ')}\nPAIN PHRASES: ${p.painPhrases.join(' | ')}\n` +
  `BUYING SIGNALS: ${p.buyingSignals.join(' | ')}\nCOMPETITORS: ${p.competitors.join(', ')}`;

// ---------------------------------------------------------------- subreddit discovery
async function redditJson(path: string): Promise<any> {
  const sep = path.includes('?') ? '&' : '?';
  const res = await fetch(`https://www.reddit.com${path}${sep}raw_json=1`, {
    credentials: 'include',
    headers: { Accept: 'application/json' },
  });
  if (!res.ok) throw new Error(String(res.status));
  return res.json();
}

interface SubFacts {
  name: string;
  subscribers: number;
  postsPerDay: number;
  description: string;
  rules: string[];
  titles: string[];
}

async function gatherSub(name: string): Promise<SubFacts | null> {
  try {
    const about = (await redditJson(`/r/${name}/about.json`)).data;
    if (!about || about.over18 || about.subreddit_type === 'private') return null;
    const newest = (await redditJson(`/r/${name}/new.json?limit=25`)).data.children.map((c: any) => c.data);
    let rules: string[] = [];
    try {
      rules = (await redditJson(`/r/${name}/about/rules.json`)).rules.map(
        (r: any) => `${r.short_name}: ${clip(r.description ?? '', 160)}`,
      );
    } catch { /* rules are optional */ }
    const times = newest.map((p: any) => p.created_utc as number);
    const span = times.length > 1 ? Math.max(1, Math.max(...times) - Math.min(...times)) : 86400;
    return {
      name: about.display_name,
      subscribers: about.subscribers ?? 0,
      postsPerDay: Math.min(500, (newest.length / span) * 86400),
      description: clip(about.public_description ?? '', 240),
      rules,
      titles: newest.slice(0, 25).map((p: any) => clip(p.title as string, 110)),
    };
  } catch {
    return null;
  }
}

export async function discoverSubreddits(
  product: Product,
  onProgress: (msg: string) => void,
): Promise<Subreddit[]> {
  const ideas = [...new Set(product.subredditIdeas.map((s) => s.toLowerCase()))].slice(0, 25);
  const facts: SubFacts[] = [];
  for (const [i, n] of ideas.entries()) {
    onProgress(`Checking r/${n} (${i + 1}/${ideas.length})`);
    const f = await gatherSub(n);
    if (f) facts.push(f);
    await sleep(700);
  }
  if (!facts.length) throw new Error('None of the suggested subreddits could be read. Are you logged in to Reddit in this browser?');

  onProgress('Scoring ICP density and rules…');
  const out: Subreddit[] = [];
  for (let i = 0; i < facts.length; i += 6) {
    const batch = facts.slice(i, i + 6);
    const r = await chatJson<{ subs: { name: string; icpDensity: number; rulesRisk: RulesRisk; reason: string }[] }>({
      system:
        'You judge whether a subreddit is a good place to find a product\'s customers. Respond in json only: ' +
        '{"subs":[{"name":str,"icpDensity":0-100,"rulesRisk":"low|medium|high","reason":str (max 20 words)}]}. ' +
        'icpDensity = share of the sampled recent post titles written by people who match the ICP with a need the product addresses. Be strict. ' +
        'rulesRisk: "high" if the rules ban self-promotion, advertising, solicitation or unsolicited DMs, or require mod approval; ' +
        '"medium" if promotion is limited (e.g. 9:1 rules, specific threads); "low" otherwise.',
      user:
        productBrief(product) +
        '\n\nSUBREDDITS:\n' +
        batch
          .map(
            (f) =>
              `### r/${f.name} (${f.subscribers} members)\n${f.description}\nRULES: ${f.rules.join(' || ') || '(none listed)'}\nRECENT TITLES:\n- ${f.titles.join('\n- ')}`,
          )
          .join('\n\n'),
      maxTokens: 2500,
    });
    for (const f of batch) {
      const j = r.subs?.find((s) => s.name.toLowerCase() === f.name.toLowerCase());
      if (!j) continue;
      const risk = (['low', 'medium', 'high'].includes(j.rulesRisk) ? j.rulesRisk : 'medium') as RulesRisk;
      const size = Math.min(100, (Math.log10(Math.max(10, f.subscribers)) / 6) * 100);
      const activity = Math.min(100, f.postsPerDay * 4);
      const score = Math.round(
        j.icpDensity * 0.6 + size * 0.1 + activity * 0.15 + (risk === 'low' ? 15 : risk === 'medium' ? 8 : 0),
      );
      out.push({
        name: f.name,
        subscribers: f.subscribers,
        postsPerDay: Math.round(f.postsPerDay * 10) / 10,
        icpDensity: j.icpDensity,
        rulesRisk: risk,
        score,
        reason: j.reason,
        enabled: risk !== 'high' && j.icpDensity >= 35,
      });
    }
  }
  return out.sort((a, b) => b.score - a.score);
}

// ---------------------------------------------------------------- lead scoring
export interface ScoreItem {
  id: string;
  kind: 'post' | 'comment';
  author: string;
  subreddit: string;
  title?: string;
  text: string;
}
export interface ScoreResult {
  id: string;
  fit: number;
  intent: string;
  reason: string;
  dm_ok: boolean;
  comment_fit: boolean;
}

export async function scoreItems(product: Product, items: ScoreItem[]): Promise<ScoreResult[]> {
  const results: ScoreResult[] = [];
  for (let i = 0; i < items.length; i += 12) {
    const batch = items.slice(i, i + 12);
    const r = await chatJson<{ results: ScoreResult[] }>({
      system:
        'You qualify Reddit authors as potential customers for a product. Respond in json only: ' +
        '{"results":[{"id":str,"fit":0-100,"intent":"asking|complaining|researching|sharing|unrelated","reason":str (max 25 words, cite what they wrote),"dm_ok":bool,"comment_fit":bool}]}. ' +
        'fit = how likely the AUTHOR matches the ICP AND has a live need visible in their text. Be strict: 70+ only with a clear, current need; ' +
        'generic chatter, news, memes and venting without a need score under 30. ' +
        'dm_ok=false for: vendors/self-promoters, bots, minors, people in distress or crisis, anyone who said they do not want DMs, or text with no personal need. ' +
        'comment_fit=true only for POSTS where a genuinely helpful, non-salesy reply that mentions the product would add value (e.g. they ask a question the product answers).',
      user:
        productBrief(product) +
        '\n\nITEMS:\n' +
        batch
          .map((it) => `[id=${it.id}] ${it.kind} by u/${it.author} in r/${it.subreddit}\n${it.title ? 'TITLE: ' + it.title + '\n' : ''}${clip(it.text, 900)}`)
          .join('\n---\n'),
      maxTokens: 3000,
    });
    results.push(...(r.results ?? []));
  }
  return results;
}

// ---------------------------------------------------------------- writing
export function renderTemplate(body: string, lead: Pick<Lead, 'username' | 'subreddit' | 'postTitle'>): string {
  const spun = body.replace(/\{([^{}|]+(?:\|[^{}]*)+)\}/g, (_, opts: string) => {
    const choices = opts.split('|').map((s) => s.trim());
    return choices[Math.floor(Math.random() * choices.length)] ?? '';
  });
  return spun
    .replace(/\{name\}/gi, lead.username)
    .replace(/\{subreddit\}/gi, lead.subreddit)
    .replace(/\{topic\}|\{title\}/gi, () =>
      clip((lead.postTitle || 'your post').replace(/[\s?.!:;,]+$/, ''), 70).replace(/^./, (c) => c.toLowerCase()),
    );
}

export async function writeDm(product: Product, lead: Lead, s: Settings): Promise<string> {
  const r = await chatJson<{ message: string }>({
    system:
      'You write the first Reddit DM from a founder to one specific person. Respond in json only: {"message":str}. Rules: ' +
      'max 60 words; plain text; sound like a real person typing, not a marketer; open with something specific they actually wrote; ' +
      'be upfront that you build the product (never pretend to be a customer or a neutral party); ' +
      'NO links, NO emojis, NO "hope this finds you well", NO flattery, NO hard pitch; ' +
      'end with one low-pressure question; do not invent facts about them or the product beyond the brief.' +
      (s.aiGuidance ? `\nFounder guidance: ${s.aiGuidance}` : ''),
    user:
      productBrief(product) +
      `\n\nRECIPIENT: u/${lead.username} in r/${lead.subreddit}\nTHEY WROTE (${lead.postTitle}): ${lead.snippet}\nWHY THEY FIT: ${lead.reason}`,
    maxTokens: 400,
    temperature: 0.9,
  });
  return (r.message ?? '').trim();
}

export async function draftComment(
  product: Product,
  post: { title: string; body: string; subreddit: string },
): Promise<{ comment: string; why: string }> {
  return chatJson({
    system:
      'You write a Reddit comment replying to a post. Respond in json only: {"comment":str,"why":str (one line)}. Rules: ' +
      'lead with real, specific help that answers their question; 40-110 words; conversational; ' +
      'mention the product only if it truly fits, in one sentence, and disclose that you build it; no links; no emojis; ' +
      'follow what the subreddit would tolerate.',
    user: `${productBrief(product)}\n\nPOST in r/${post.subreddit}\nTITLE: ${post.title}\n${clip(post.body, 1200)}`,
    maxTokens: 500,
    temperature: 0.8,
  });
}

export async function generatePostIdeas(product: Product, subs: Subreddit[]): Promise<PostIdea[]> {
  const r = await chatJson<{ ideas: Omit<PostIdea, 'id'>[] }>({
    system:
      'You propose Reddit posts that earn attention by giving value first, then let the product come up naturally. ' +
      'Respond in json only: {"ideas":[{"subreddit":str,"title":str,"body":str (80-200 words),"angle":str (one line)}]}. ' +
      'Exactly 5 ideas across the given subreddits. No ad copy, no links, written like a founder sharing what they learned.',
    user: `${productBrief(product)}\n\nSUBREDDITS: ${subs.map((s) => 'r/' + s.name).join(', ')}`,
    maxTokens: 3000,
    temperature: 0.9,
  });
  return (r.ideas ?? []).map((i, n) => ({ ...i, id: `${Date.now()}-${n}` }));
}

// ---------------------------------------------------------------- spintax
const VARS = ['{name}', '{subreddit}', '{topic}', '{title}'];
const SPIN_RE = /\{([^{}|]+(?:\|[^{}]*)+)\}/g;

/** How many distinct messages a template can render. */
export function countVariants(body: string): number {
  let n = 1;
  for (const m of body.matchAll(SPIN_RE)) n *= (m[1] ?? '').split('|').length;
  return n;
}

/** Render N different previews of a template for a sample lead. */
export function previewVariants(body: string, sample: Pick<Lead, 'username' | 'subreddit' | 'postTitle'>, n = 4): string[] {
  const seen = new Set<string>();
  for (let i = 0; i < n * 6 && seen.size < n; i++) seen.add(renderTemplate(body, sample));
  return [...seen];
}

function validSpintax(original: string, candidate: string): string | null {
  // removing every innermost {...} group must leave no braces => balanced, not nested
  if (/[{}]/.test(candidate.replace(/\{[^{}]*\}/g, ''))) return 'unbalanced or nested braces';
  const count = (s: string, v: string) => s.split(v).length - 1;
  for (const v of VARS) {
    if (count(candidate, v) < count(original, v)) return `lost variable ${v}`;
  }
  if (/https?:\/\/|www\./i.test(candidate)) return 'contains a link';
  if (countVariants(candidate) < 8) return 'too few variations';
  const leftovers = candidate.replace(SPIN_RE, '').match(/\{[^{}]*\}/g) ?? [];
  if (leftovers.some((l) => !VARS.includes(l.toLowerCase()))) return `unknown placeholder ${leftovers.join(',')}`;
  return null;
}

/**
 * Rewrite a message template with {a|b|c} spintax so no two DMs read the same.
 * Variables ({name}, {subreddit}, {topic}) are preserved; output is validated and retried once.
 */
export async function addSpintax(body: string, intensity: 'light' | 'medium' | 'heavy' = 'medium'): Promise<string> {
  const spots = { light: '4-6', medium: '7-10', heavy: '11-15' }[intensity];
  let feedback = '';
  for (let attempt = 0; attempt < 2; attempt++) {
    const r = await chatJson<{ template: string }>({
      system:
        'You are a spintax writer for cold outreach messages. Rewrite the user\'s message so it contains spintax: ' +
        'groups written {option one|option two|option three} where the renderer picks one option at random. Respond in json only: {"template":str}. Rules: ' +
        `use ${spots} spintax groups, each with 2-4 options; spin greetings, openers, connectors, the ask, and sign-offs, plus a few mid-sentence phrases; ` +
        'EVERY option in a group must make the sentence grammatical and keep the exact meaning and tone, so any combination reads naturally; ' +
        'options may differ in length but must not change facts or add claims; ' +
        'never nest groups (no braces inside a group); never add links or emojis; ' +
        'keep these placeholders exactly as written and outside of spin groups where possible: {name} {subreddit} {topic} {title}; ' +
        'keep it sounding like one real person typing, not a template. Preserve line breaks.' +
        (feedback ? `\nYour previous attempt was rejected: ${feedback}. Fix that.` : ''),
      user: `MESSAGE:\n${body}`,
      maxTokens: 1500,
      temperature: 0.8,
    });
    const t = (r.template ?? '').trim();
    const problem = t ? validSpintax(body, t) : 'empty result';
    if (!problem) return t;
    feedback = problem;
  }
  throw new Error(`Spintax writer could not produce a valid template (${feedback}). Try again or edit by hand.`);
}
