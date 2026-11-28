import type { CommentData, PostData } from '../types';
import { deepQueryAll } from './dom';

const attr = (el: Element, n: string) => el.getAttribute(n) ?? '';
const num = (s: string) => Number.parseInt(s.replace(/[^\d-]/g, ''), 10) || 0;
const BOT = /(^automoderator$|bot$|^\[deleted\]$|^reddit$)/i;
export const isHuman = (u: string) => !!u && !BOT.test(u);

const norm = (p: string) => p.replace(/^https?:\/\/[^/]+/, '').replace(/\/$/, '') + '/';

export function readPosts(): PostData[] {
  const out: PostData[] = [];
  for (const el of deepQueryAll('shreddit-post')) {
    const permalink = attr(el, 'permalink');
    if (!permalink) continue;
    out.push({
      id: norm(permalink),
      title: attr(el, 'post-title'),
      author: attr(el, 'author'),
      subreddit: attr(el, 'subreddit-prefixed-name').replace(/^r\//, ''),
      permalink: norm(permalink),
      score: num(attr(el, 'score')),
      comments: num(attr(el, 'comment-count')),
      body: (el.querySelector('[slot="text-body"]') as HTMLElement | null)?.innerText?.trim() ?? '',
    });
  }
  if (out.length) return out;
  // old.reddit fallback
  for (const el of Array.from(document.querySelectorAll('.thing.link'))) {
    const permalink = attr(el, 'data-permalink');
    out.push({
      id: norm(permalink),
      title: (el.querySelector('a.title') as HTMLElement | null)?.innerText ?? '',
      author: attr(el, 'data-author'),
      subreddit: attr(el, 'data-subreddit'),
      permalink: norm(permalink),
      score: num(attr(el, 'data-score')),
      comments: num(attr(el, 'data-comments-count')),
      body: (el.querySelector('.usertext-body') as HTMLElement | null)?.innerText?.trim() ?? '',
    });
  }
  return out;
}

export function readComments(): CommentData[] {
  const out: CommentData[] = [];
  for (const el of deepQueryAll('shreddit-comment')) {
    const text = (el.querySelector('[slot="comment"]') as HTMLElement | null)?.innerText?.trim() ?? '';
    const permalink = attr(el, 'permalink');
    if (!text || !permalink) continue;
    out.push({
      id: norm(permalink),
      author: attr(el, 'author'),
      permalink: norm(permalink),
      depth: num(attr(el, 'depth')),
      score: num(attr(el, 'score')),
      text,
    });
  }
  if (out.length) return out;
  for (const el of Array.from(document.querySelectorAll('.thing.comment'))) {
    const permalink = attr(el, 'data-permalink');
    out.push({
      id: norm(permalink),
      author: attr(el, 'data-author'),
      permalink: norm(permalink),
      depth: 0,
      score: 0,
      text: (el.querySelector('.usertext-body') as HTMLElement | null)?.innerText?.trim() ?? '',
    });
  }
  return out.filter((c) => c.text);
}
