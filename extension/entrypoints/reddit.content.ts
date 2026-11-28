import { defineContentScript } from 'wxt/utils/define-content-script';
import type { CommentData, DmResult, PostData } from '../lib/types';
import { GhostCursor } from '../lib/page/cursor';
import { deepQueryAll, editorText, findClickables, inViewport, isVisible, pageText, typeInto, typoLog } from '../lib/page/dom';
import { AbortError, clamp, lognormal, pick, rand, setAborted, sleep, smoothScrollBy, waitFor } from '../lib/page/human';
import { persona, t, userPace } from '../lib/page/persona';
import { isHuman, readComments, readPosts } from '../lib/page/reader';

const cursor = new GhostCursor();

// dwell time on a title/snippet: depends on its length, this session's patience, and plain randomness
const readTime = (text: string) => clamp(t(text.length * lognormal(28, 0.4) * persona.patience, 0.45), 500, 9000);
const pathOf = (u: string) => u.replace(/^https?:\/\/[^/]+/, '').toLowerCase().replace(/\/$/, '');

// ------------------------------------------------------------------ browse a feed / listing
async function browse(maxPosts: number, seconds: number): Promise<PostData[]> {
  const seen = new Map<string, PostData>();
  const end = Date.now() + seconds * 1000;
  let stale = 0;
  while (Date.now() < end && seen.size < maxPosts && stale < 4) {
    const before = seen.size;
    for (const p of readPosts()) if (isHuman(p.author)) seen.set(p.id, p);
    stale = seen.size === before ? stale + 1 : 0;

    const visible = (deepQueryAll('shreddit-post') as HTMLElement[]).filter(inViewport);
    if (visible.length && Math.random() < 0.75) {
      const el = pick(visible);
      const title = el.getAttribute('post-title') ?? '';
      await cursor.hover(el);
      await cursor.linger(readTime(title));
    } else if (Math.random() < 0.4) {
      await cursor.fidget();
    }
    if (Math.random() < 0.07) await cursor.idleClick();
    await smoothScrollBy(lognormal(520, 0.38) * (Math.random() < 0.08 ? -0.5 : 1));
    await sleep(t(Math.random() < 0.12 ? 2600 : 850, 0.6)); // now and then stop and actually read
  }
  for (const p of readPosts()) if (isHuman(p.author)) seen.set(p.id, p);
  return [...seen.values()];
}

// ------------------------------------------------------------------ read an opened post + comments
async function readPost(): Promise<{ post: PostData | null; comments: CommentData[] }> {
  await waitFor(() => readPosts()[0], 8000);
  const post = readPosts()[0] ?? null;
  await sleep(readTime(((post?.title ?? '') + (post?.body ?? '')).slice(0, 400)));
  const comments = new Map<string, CommentData>();
  for (let i = 0; i < 4; i++) {
    for (const c of readComments()) if (isHuman(c.author) && c.depth <= 2) comments.set(c.id, c);
    const els = (deepQueryAll('shreddit-comment') as HTMLElement[]).filter(inViewport);
    if (els.length) {
      const target = pick(els);
      await cursor.hover(target);
      await cursor.linger(readTime((target.textContent ?? '').slice(0, 300)));
    }
    if (Math.random() < 0.05) await cursor.idleClick();
    await smoothScrollBy(lognormal(560, 0.38));
    await sleep(t(900, 0.55));
  }
  for (const c of readComments()) if (isHuman(c.author) && c.depth <= 2) comments.set(c.id, c);
  return { post, comments: [...comments.values()].slice(0, 40) };
}

// ------------------------------------------------------------------ click a link (navigates asynchronously)
function findLink(hrefIncludes: string): HTMLAnchorElement | null {
  const needle = hrefIncludes.toLowerCase();
  const links = deepQueryAll('a[href]') as HTMLAnchorElement[];
  const hits = links.filter((a) => pathOf(a.getAttribute('href') ?? '').includes(needle) && a.getClientRects().length > 0);
  return hits.find(isVisible) ?? hits[0] ?? null;
}

function clickLinkAsync(hrefIncludes: string): { ok: boolean } {
  const a = findLink(hrefIncludes);
  if (!a) return { ok: false };
  // The click destroys this page, so answer first and click in the background.
  setTimeout(() => cursor.click(a).catch(() => {}), 0);
  return { ok: true };
}

// ------------------------------------------------------------------ upvote
async function upvote(scope: 'post' | 'comment', permalink?: string): Promise<{ ok: boolean; already?: boolean }> {
  const host =
    scope === 'post'
      ? (deepQueryAll('shreddit-post') as HTMLElement[]).find((el) => !permalink || pathOf(el.getAttribute('permalink') ?? '') === pathOf(permalink))
      : (deepQueryAll('shreddit-comment') as HTMLElement[]).find((el) => pathOf(el.getAttribute('permalink') ?? '') === pathOf(permalink ?? ''));
  if (!host) return { ok: false };
  const btn =
    deepQueryAll('button[upvote], [aria-label="Upvote"], button[aria-label*="pvote" i]', host).find(isVisible) as HTMLElement | undefined;
  if (!btn) return { ok: false };
  if (btn.getAttribute('aria-pressed') === 'true') return { ok: true, already: true };
  await cursor.click(btn);
  await sleep(t(550, 0.5));
  return { ok: true };
}

// ------------------------------------------------------------------ join a subreddit
async function join(): Promise<{ ok: boolean; already?: boolean }> {
  const state = await waitFor(() => findClickables(/^(join|joined)$/i, '')[0], 7000);
  if (!state) return { ok: false };
  if (/^joined$/i.test((state.innerText || '').trim()) || /^joined\b/i.test(state.getAttribute('aria-label') ?? '')) return { ok: true, already: true };
  await cursor.linger(t(900, 0.5));
  await cursor.click(state);
  await sleep(t(900, 0.5));
  const joined = await waitFor(() => findClickables(/^joined$/i, '')[0], 4000);
  return joined ? { ok: true } : { ok: false };
}

// ------------------------------------------------------------------ DM
const RATE = /(you'?re|you are) doing that too much|rate.?limit|try again (later|in)|too many (messages|requests)/i;
const BLOCK = /(can'?t|cannot|unable to) (send|message|chat)|isn'?t accepting|has blocked you|not accepting (messages|chats)|only (accepts|allows)/i;
const CAPTCHA = /captcha|verify (that )?you'?re (a )?human/i;

function classifyPage(before: string): DmResult | null {
  const now = pageText();
  // Compare match COUNTS, not presence: a username/bio that already contains "rate limit" must not mask a real banner.
  const count = (re: RegExp, s: string) => (s.match(new RegExp(re.source, 'gi')) ?? []).length;
  const fresh = (re: RegExp) => count(re, now) > count(re, before);
  if (fresh(CAPTCHA)) return { ok: false, code: 'captcha', error: 'Captcha shown' };
  if (fresh(RATE)) return { ok: false, code: 'rate-limit', error: 'Reddit is rate limiting messages' };
  if (fresh(BLOCK)) return { ok: false, code: 'blocked', error: 'User cannot be messaged' };
  return null;
}

function findComposer(): HTMLElement | null {
  const cands = (deepQueryAll('textarea, [contenteditable="true"], [role="textbox"]') as HTMLElement[]).filter(isVisible);
  const hint = /message|chat|type a|write a/i;
  return (
    cands.find((el) => hint.test(`${el.getAttribute('placeholder') ?? ''} ${el.getAttribute('aria-label') ?? ''} ${el.getAttribute('data-placeholder') ?? ''}`)) ??
    cands[cands.length - 1] ??
    null
  );
}

async function sendViaComposer(text: string, before: string): Promise<DmResult> {
  const composer = await waitFor(findComposer, 12000, 400);
  if (!composer) return { ok: false, code: 'no-composer', error: 'Chat composer did not appear' };
  await cursor.click(composer);
  await sleep(t(550, 0.5));
  await typeInto(composer, text, 'dm');
  // read it back before sending: longer for longer messages, sometimes a second look
  await sleep(clamp(t(800 + text.length * 14, 0.5), 600, 9000));
  if (Math.random() < 0.18) await sleep(t(1400, 0.6));

  const send = findClickables(/^send( message)?$/i, '')[0];
  if (send) {
    await cursor.click(send);
  } else {
    for (const type of ['keydown', 'keypress', 'keyup']) {
      composer.dispatchEvent(new KeyboardEvent(type, { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, composed: true }));
    }
  }

  const cleared = await waitFor(() => !composer.isConnected || editorText(composer).trim() === '', 7000, 300);
  await sleep(600);
  const bad = classifyPage(before);
  if (bad) return bad;
  if (!cleared) return { ok: false, code: 'send-failed', error: 'Message was typed but did not send' };
  return { ok: true };
}

async function dm(text: string): Promise<DmResult> {
  const before = pageText();
  const captcha = classifyPage(before);
  if (captcha) return captcha;

  // Legacy compose page (/message/compose?to=user)
  if (location.pathname.startsWith('/message/compose')) {
    const subject = document.querySelector('input[name="subject"]') as HTMLInputElement | null;
    const body = document.querySelector('textarea[name="text"], textarea[name="message"]') as HTMLTextAreaElement | null;
    if (!subject || !body) return { ok: false, code: 'no-composer', error: 'Compose form not found' };
    await cursor.click(subject);
    await typeInto(subject, text.split(/\s+/).slice(0, 5).join(' '), 'field');
    await cursor.click(body);
    await typeInto(body, text, 'dm');
    const submit = findClickables(/^send$/i, '')[0] ?? (document.querySelector('button[type="submit"]') as HTMLElement | null);
    if (!submit) return { ok: false, code: 'send-failed', error: 'Send button not found' };
    await cursor.click(submit);
    await sleep(t(2500, 0.3));
    const bad = classifyPage(before);
    if (bad) return bad;
    return /message (has been )?(sent|delivered)|your message/i.test(pageText()) ? { ok: true } : { ok: false, code: 'send-failed', error: 'No delivery confirmation' };
  }

  // Profile page -> Chat button
  if (/page not found|this account has been suspended|nobody on reddit goes by that name/i.test(pageText())) {
    return { ok: false, code: 'blocked', error: 'Profile unavailable' };
  }
  await cursor.linger(t(2200, 0.5)); // look at the profile first
  if (Math.random() < 0.1) await cursor.idleClick();
  if (Math.random() < 0.7) await smoothScrollBy(lognormal(150, 0.5));
  await sleep(t(700, 0.5));
  const btn = await waitFor(
    () => findClickables(/^(start chat|chat|message|send message)$/i)[0] ?? findClickables(/\b(start (a )?chat|send (a )?message)\b/i)[0],
    7000,
  );
  if (!btn) return { ok: false, code: 'no-chat-button', error: 'No chat/message button on profile' };
  await cursor.click(btn);
  await sleep(t(1300, 0.45));
  return sendViaComposer(text, before);
}

// ------------------------------------------------------------------ comment
async function postComment(text: string): Promise<{ ok: boolean; error?: string }> {
  const before = pageText();
  const trigger = await waitFor(
    () =>
      (deepQueryAll('faceplate-textarea-input, [placeholder*="conversation" i], comment-composer-host, [data-test-id="comment-composer"]') as HTMLElement[]).find(isVisible),
    8000,
  );
  if (!trigger) return { ok: false, error: 'Comment box not found (is the post locked?)' };
  await sleep(t(1800, 0.5)); // re-read the post before replying
  await cursor.click(trigger);
  await sleep(t(900, 0.45));
  const editor = await waitFor(
    () => (deepQueryAll('[contenteditable="true"], textarea') as HTMLElement[]).filter(isVisible).pop(),
    5000,
  );
  if (!editor) return { ok: false, error: 'Comment editor did not open' };
  await typeInto(editor, text, 'comment');
  await sleep(clamp(t(1500 + text.length * 20, 0.5), 900, 14000)); // proofread
  const submit = findClickables(/^comment$/i, '').pop();
  if (!submit) return { ok: false, error: 'Comment button not found' };
  await cursor.click(submit);
  const cleared = await waitFor(() => !editor.isConnected || editorText(editor).trim() === '', 7000, 300);
  const bad = classifyPage(before);
  if (bad) return { ok: false, error: bad.error };
  return cleared ? { ok: true } : { ok: false, error: 'Comment did not post' };
}

// ------------------------------------------------------------------ submit a text post
const SUBMIT_ERRORS = [
  /(this community|r\/\w+) requires? (a )?(post )?flair/i, /(add|select|choose) (a )?flair/i,
  /you'?re doing that too much|try again (later|in)|rate.?limit/i,
  /(not allowed|isn'?t allowed|can'?t|cannot|unable to) (post|submit)/i, /(banned|restricted|muted) (from|in)/i,
  /must be approved|requires? approval/i, /title.{0,20}(required|too (short|long)|must)/i,
  /(post|submission).{0,30}(removed|rejected)/i, /captcha|verify (that )?you'?re (a )?human/i,
];
const hintOf = (el: HTMLElement) =>
  `${el.getAttribute('placeholder') ?? ''} ${el.getAttribute('aria-label') ?? ''} ${el.getAttribute('name') ?? ''} ${el.getAttribute('data-placeholder') ?? ''}`;

/** What the page is complaining about (flair required, rate limit, ...), for when a submit didn't go through. */
function submitErrors(): string[] {
  const text = pageText();
  return [...new Set(SUBMIT_ERRORS.map((re) => text.match(re)?.[0]).filter((x): x is string => !!x))];
}

function findTitleField(): HTMLElement | null {
  const c = (deepQueryAll('textarea, input[type="text"], input:not([type]), [contenteditable="true"], [role="textbox"]') as HTMLElement[]).filter(isVisible);
  return c.find((el) => /title/i.test(hintOf(el))) ?? null;
}

function findBodyEditor(title: HTMLElement): HTMLElement | null {
  const c = (deepQueryAll('[contenteditable="true"], [role="textbox"], textarea') as HTMLElement[]).filter(isVisible).filter((el) => el !== title && !/title/i.test(hintOf(el)));
  return c.find((el) => /body|text|content|description|optional/i.test(hintOf(el))) ?? c[c.length - 1] ?? null;
}

/** Fill in and press "Post" on a subreddit's submit page. Success is the redirect to the new thread, which the caller watches. */
async function submitPost(title: string, body: string): Promise<{ ok: boolean; code?: string; error?: string; errors?: string[] }> {
  if (/captcha|verify (that )?you'?re (a )?human/i.test(pageText())) return { ok: false, code: 'captcha', error: 'Captcha shown' };
  const titleEl = await waitFor(findTitleField, 12000, 400);
  if (!titleEl) return { ok: false, code: 'no-title', error: 'Title field not found (is this a submit page, and is the subreddit open to you?)' };
  await sleep(t(900, 0.5));
  await cursor.click(titleEl);
  await sleep(t(500, 0.5));
  await typeInto(titleEl, title, 'field');
  await sleep(clamp(t(900 + title.length * 12, 0.5), 600, 6000));

  if (body.trim()) {
    const editor = await waitFor(() => findBodyEditor(titleEl), 6000, 300);
    if (!editor) return { ok: false, code: 'no-body', error: 'Body editor not found (does this subreddit allow text posts?)' };
    await cursor.click(editor);
    await sleep(t(600, 0.5));
    await typeInto(editor, body, 'comment');
    await sleep(clamp(t(2000 + body.length * 18, 0.5), 1200, 16000)); // proofread
  }

  const post = await waitFor(() => findClickables(/^post$/i, '').pop(), 6000, 300);
  if (!post) return { ok: false, code: 'no-post-button', error: 'Post button not found' };
  const disabled = () => (post as HTMLButtonElement).disabled || post.getAttribute('aria-disabled') === 'true';
  if (disabled()) await waitFor(() => !disabled(), 4000, 300); // some pages enable it a moment after typing
  if (disabled()) return { ok: false, code: 'disabled', error: 'The Post button is disabled', errors: submitErrors() };
  await cursor.click(post);
  return { ok: true };
}

// ------------------------------------------------------------------ doctor: read-only checks of Reddit's markup
interface Check { name: string; ok: boolean; detail: string }

/**
 * Does this page look the way Pigeon expects? Reads only: never types, clicks or sends. The caller visits a feed, a post,
 * a profile, the chat list and the submit page and runs this on each, so a Reddit markup change shows up as one clearly
 * named failure instead of a mystery halfway through a session.
 */
function probePage(kind: string) {
  const checks: Check[] = [];
  const add = (name: string, ok: boolean, detail = '') => checks.push({ name, ok, detail });
  const posts = readPosts();
  const sample: Record<string, unknown> = {};

  add('logged in (no "Log In" button)', findClickables(/^log ?in$/i, '').length === 0);

  if (kind === 'feed' || kind === 'post') {
    const raw = deepQueryAll('shreddit-post').length + document.querySelectorAll('.thing.link').length;
    add('post elements (<shreddit-post>)', raw > 0, `${raw} found`);
    const bad = posts.filter((p) => !p.author || !p.title || !p.permalink).length;
    add('posts parse (author, title, permalink)', posts.length > 0 && bad === 0, `${posts.length} parsed${bad ? `, ${bad} missing a field` : ''}`);
    if (posts[0]) Object.assign(sample, { author: posts[0].author, title: posts[0].title.slice(0, 60), permalink: posts[0].permalink, subreddit: posts[0].subreddit });
    const up = (deepQueryAll('button[upvote], [aria-label="Upvote"], button[aria-label*="pvote" i]') as HTMLElement[]).filter(isVisible);
    add('upvote button', up.length > 0, `${up.length} visible`);
  }
  if (kind === 'post') {
    const comments = readComments();
    add('comments parse', comments.length > 0 || /no comments yet/i.test(pageText()), `${comments.length} parsed`);
    add('comment box', !!(deepQueryAll('faceplate-textarea-input, [placeholder*="conversation" i], comment-composer-host, [data-test-id="comment-composer"]') as HTMLElement[]).find(isVisible));
  }
  if (kind === 'profile') {
    add('profile exists', !/page not found|this account has been suspended|nobody on reddit goes by that name/i.test(pageText()));
    add('chat / message button', !!(findClickables(/^(start chat|chat|message|send message)$/i)[0] ?? findClickables(/\b(start (a )?chat|send (a )?message)\b/i)[0]));
  }
  if (kind === 'chat') {
    const rooms = scanInbox();
    add('chat rooms list', rooms.length > 0, `${rooms.length} rooms, ${rooms.filter((r) => r.unread).length} unread`);
    sample.rooms = rooms.slice(0, 3).map((r) => r.text.slice(0, 70));
  }
  if (kind === 'submit') {
    const title = findTitleField();
    add('title field', !!title);
    add('body editor', !!(title && findBodyEditor(title)));
    add('Post button', findClickables(/^post$/i, '').length > 0);
  }
  return { ok: true, kind, url: location.href, checks, sample };
}

// ------------------------------------------------------------------ scan (Page DMer) + inbox
function scanPage() {
  const out: { username: string; subreddit: string; postUrl: string; postTitle: string; snippet: string }[] = [];
  const posts = readPosts();
  for (const p of posts) if (isHuman(p.author)) out.push({ username: p.author, subreddit: p.subreddit, postUrl: p.permalink, postTitle: p.title, snippet: `${p.title}\n${p.body}`.slice(0, 600) });
  const here = posts[0];
  for (const c of readComments())
    if (isHuman(c.author))
      out.push({ username: c.author, subreddit: here?.subreddit ?? '', postUrl: c.permalink, postTitle: here?.title ?? '', snippet: c.text.slice(0, 600) });
  return out;
}

function scanInbox() {
  const rooms = (deepQueryAll('a[href*="/chat/"], a[href*="/room/"], [role="listitem"], [role="option"]') as HTMLElement[])
    .filter(isVisible)
    .map((el) => ({
      text: (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 240),
      unread: /unread/i.test(el.getAttribute('aria-label') ?? '') || !!el.querySelector('[class*="unread" i], [aria-label*="unread" i]'),
    }))
    .filter((r) => r.text);
  return rooms;
}

/** Run one command from the extension (or, in the test-only build, from a test page). Never throws: errors come back as {ok:false}. */
async function handle(msg: any): Promise<unknown> {
  if (msg.cmd === 'abort') {
    setAborted(true);
    cursor.hud('');
    return { ok: true };
  }
  setAborted(false);
  try {
    switch (msg.cmd) {
      case 'ping': return { ok: true, url: location.href };
      case 'url': return { ok: true, url: location.href };
      case 'hud': cursor.hud(msg.text ?? ''); return { ok: true };
      case 'idleClick': return { ok: await cursor.idleClick() };
      case 'moveCursor': await cursor.moveTo(msg.x, msg.y); return { ok: true };
      case 'clickSelector': { // diagnostics: click a matching element with the human cursor
        const el = deepQueryAll(msg.selector).find(isVisible);
        if (!el) return { ok: false };
        await cursor.click(el);
        return { ok: true };
      }
      case 'typoLog': return { ok: true, log: typoLog.splice(0) };
      case 'browse': return { ok: true, posts: await browse(msg.maxPosts ?? 25, msg.seconds ?? 35) };
      case 'readPost': return { ok: true, ...(await readPost()) };
      case 'clickLink': return clickLinkAsync(msg.hrefIncludes);
      case 'upvote': return upvote(msg.scope, msg.permalink);
      case 'join': return join();
      case 'dm': return dm(msg.text);
      case 'postComment': return postComment(msg.text);
      case 'submitPost': return submitPost(msg.title, msg.body ?? '');
      case 'submitErrors': return { ok: true, errors: submitErrors() };
      case 'probe': return probePage(msg.kind);
      case 'scanPage': return { ok: true, items: scanPage() };
      case 'scanInbox': return { ok: true, rooms: scanInbox() };
      default: return { ok: false, error: 'unknown command ' + msg.cmd };
    }
  } catch (e) {
    return { ok: false, aborted: e instanceof AbortError, error: String((e as Error)?.message ?? e) };
  }
}

// ------------------------------------------------------------------ wiring
export default defineContentScript({
  matches: ['*://*.reddit.com/*'],
  runAt: 'document_idle',
  main() {
    cursor.mount(() => chrome.runtime.sendMessage({ type: 'user-stop' }));
    const applyPace = (s: { pace?: number } | undefined) => (userPace.v = clamp(s?.pace ?? 1, 0.5, 2));
    chrome.storage.local.get('settings').then((r) => applyPace(r.settings as { pace?: number } | undefined));
    chrome.storage.onChanged.addListener((ch) => ch.settings && applyPace(ch.settings.newValue as { pace?: number } | undefined));
    chrome.runtime.sendMessage({ type: 'content-ready' }).catch(() => {});

    chrome.runtime.onMessage.addListener((msg, _sender, send) => {
      if (msg?.target !== 'page') return;
      handle(msg).then(send);
      return true;
    });

    // Test-only build (`wxt build -m testbridge`): lets a test page run commands in the REAL extension content-script world, so
    // Firefox's sandbox can be exercised without driving the extension's own pages. Compiled out of every normal build.
    if (import.meta.env.MODE === 'testbridge') {
      window.addEventListener('message', (e) => {
        if (e.source !== window || e.data?.__pigeonTest !== 'cmd') return;
        handle({ ...e.data.msg, target: 'page' }).then((res) => window.postMessage({ __pigeonTest: 'res', id: e.data.id, res: JSON.parse(JSON.stringify(res ?? null)) }, '*'));
      });
    }
  },
});
