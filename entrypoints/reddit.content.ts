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
      if (msg.cmd === 'abort') {
        setAborted(true);
        cursor.hud('');
        send({ ok: true });
        return;
      }
      setAborted(false);
      (async () => {
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
          case 'dm': return dm(msg.text);
          case 'postComment': return postComment(msg.text);
          case 'scanPage': return { ok: true, items: scanPage() };
          case 'scanInbox': return { ok: true, rooms: scanInbox() };
          default: return { ok: false, error: 'unknown command ' + msg.cmd };
        }
      })()
        .then(send)
        .catch((e) => send({ ok: false, aborted: e instanceof AbortError, error: String(e?.message ?? e) }));
      return true;
    });
  },
});
