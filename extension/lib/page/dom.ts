import { clamp, gauss, lognormal, rand, sleep } from './human';
import { persona, t, tempo } from './persona';

/** querySelectorAll that also descends into open shadow roots (Reddit is built on web components). */
export function deepQueryAll(selector: string, root: Document | Element | ShadowRoot = document): Element[] {
  const out: Element[] = [];
  const walk = (node: ParentNode) => {
    out.push(...Array.from(node.querySelectorAll(selector)));
    for (const el of Array.from(node.querySelectorAll('*'))) {
      if (el.shadowRoot) walk(el.shadowRoot);
    }
  };
  if (root instanceof Element && root.shadowRoot) walk(root.shadowRoot);
  walk(root);
  return out;
}

export const deepQuery = (selector: string, root?: Document | Element | ShadowRoot) =>
  deepQueryAll(selector, root)[0] ?? null;

export function isVisible(el: Element): boolean {
  const r = el.getBoundingClientRect();
  if (r.width < 2 || r.height < 2) return false;
  const cs = getComputedStyle(el);
  return cs.visibility !== 'hidden' && cs.display !== 'none' && cs.opacity !== '0';
}

export const inViewport = (el: Element) => {
  const r = el.getBoundingClientRect();
  return r.bottom > 60 && r.top < window.innerHeight - 40 && r.width > 0;
};

export const labelOf = (el: Element) =>
  `${el.getAttribute('aria-label') ?? ''} ${(el as HTMLElement).innerText ?? el.textContent ?? ''}`.trim();

/** Find clickable things (buttons, links, role=button) whose aria-label OR visible text matches. */
export function findClickables(re: RegExp, excludeInside = 'header, nav'): HTMLElement[] {
  const n = (s: string | null | undefined) => (s ?? '').replace(/\s+/g, ' ').trim();
  return (deepQueryAll('button, a, [role="button"]') as HTMLElement[])
    .filter((el) => isVisible(el) && (re.test(n(el.getAttribute('aria-label'))) || re.test(n(el.innerText))))
    .filter((el) => !excludeInside || !el.closest(excludeInside));
}

export function pageText(): string {
  return (document.body.innerText || '').toLowerCase();
}

function setCaretToEnd(el: HTMLElement) {
  const sel = window.getSelection();
  if (!sel) return;
  const range = document.createRange();
  range.selectNodeContents(el);
  range.collapse(false);
  sel.removeAllRanges();
  sel.addRange(range);
}

const ROWS = ['qwertyuiop', 'asdfghjkl', 'zxcvbnm'];
function wrongKey(ch: string): string {
  const lower = ch.toLowerCase();
  for (const row of ROWS) {
    const i = row.indexOf(lower);
    if (i >= 0) {
      const opts = [row[i - 1], row[i + 1]].filter(Boolean) as string[];
      const w = opts[Math.floor(Math.random() * opts.length)] ?? lower;
      return ch === lower ? w : w.toUpperCase();
    }
  }
  return ch;
}

export type TypeKind = 'dm' | 'comment' | 'field';

/** Every typo made (for tests / diagnostics). */
export const typoLog: string[] = [];

const FAST_BIGRAMS = new Set(['th', 'he', 'in', 'er', 'an', 're', 'on', 'at', 'en', 'nd', 'ti', 'es', 'or', 'te', 'of', 'ed', 'is', 'it', 'al', 'ar', 'st', 'to', 'nt', 'ng']);

/**
 * Type like a person, differently every time:
 *  - speed drifts continuously and also swings between fast bursts and slow patches
 *  - each word has its own rhythm; common letter pairs fly, long words get a beat of hesitation
 *  - stops to think mid-message; pauses after punctuation
 *  - typos of several kinds (neighbouring key, swapped letters, doubled letter, skipped letter), noticed at once
 *    or a few characters/word later, then backspaced and retyped.
 * The final text always equals `text`.
 */
export async function typeInto(el: HTMLElement, text: string, kind: TypeKind = 'dm'): Promise<void> {
  el.focus();
  const isInput = el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement;
  if (!isInput) setCaretToEnd(el);

  const fire = (type: string, key: string) =>
    el.dispatchEvent(new KeyboardEvent(type, { key, bubbles: true, composed: true }));

  const insert = (ch: string) => {
    if (isInput) {
      const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value')!.set!;
      setter.call(el, (el as HTMLInputElement).value + ch);
      el.dispatchEvent(new InputEvent('input', { bubbles: true, composed: true, data: ch, inputType: 'insertText' }));
    } else if (ch === '\n') {
      document.execCommand('insertLineBreak');
    } else if (!document.execCommand('insertText', false, ch)) {
      el.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, cancelable: true, composed: true, data: ch, inputType: 'insertText' }));
      el.textContent = (el.textContent ?? '') + ch;
      el.dispatchEvent(new InputEvent('input', { bubbles: true, composed: true, data: ch, inputType: 'insertText' }));
    }
  };

  const erase = () => {
    if (isInput) {
      const cur = (el as HTMLInputElement).value;
      const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value')!.set!;
      setter.call(el, cur.slice(0, -1));
      el.dispatchEvent(new InputEvent('input', { bubbles: true, composed: true, inputType: 'deleteContentBackward' }));
    } else {
      setCaretToEnd(el);
      document.execCommand('delete');
    }
  };

  const baseMs = 1000 / persona.typingCps;
  let drift = 1; // slow wander of overall speed
  let fast = Math.random() < 0.5;
  let untilSwitch = Math.round(rand(4, 24));
  let wordFactor = lognormal(1, 0.28);
  let prev = '';

  const keystroke = async (ch: string, boost = 1) => {
    drift = clamp(1 + 0.9 * (drift - 1) + gauss(0, 0.075), 0.6, 1.7);
    if (prev === ' ' || prev === '') wordFactor = lognormal(1, 0.28);
    let d = lognormal(baseMs, 0.3) * tempo() * drift * wordFactor * boost;
    d *= fast ? lognormal(0.62, 0.15) : lognormal(1.3, 0.15);
    if (FAST_BIGRAMS.has((prev + ch).toLowerCase())) d *= 0.8;
    if (ch === ' ') d *= lognormal(1.25, 0.3);
    else if (ch !== ch.toLowerCase()) d *= 1.25; // shift
    if (ch === prev) d *= 0.8;
    if (/[.,!?;:]/.test(prev)) d += lognormal(240, 0.55) * tempo();
    if (--untilSwitch <= 0) {
      fast = !fast;
      untilSwitch = Math.round(rand(4, 24));
    }
    if (Math.random() < (kind === 'comment' ? 0.014 : kind === 'dm' ? 0.007 : 0)) {
      d += lognormal(kind === 'comment' ? 1500 : 950, 0.55) * tempo(); // stops to think
    }
    await sleep(clamp(d, 20, 6000));
    fire('keydown', ch === '\n' ? 'Enter' : ch);
    insert(ch);
    await sleep(clamp(lognormal(42, 0.35), 15, 140));
    fire('keyup', ch === '\n' ? 'Enter' : ch);
    prev = ch;
  };

  const backspaces = async (n: number) => {
    for (let j = 0; j < n; j++) {
      await sleep(clamp(lognormal(j === 0 ? 170 : 95, 0.35) * tempo() * (j === 0 ? 1 : 0.6), 35, 520));
      fire('keydown', 'Backspace');
      erase();
      await sleep(clamp(lognormal(40, 0.3), 15, 120));
      fire('keyup', 'Backspace');
    }
  };

  const lead = kind === 'comment' ? 1800 + text.length * 16 : kind === 'dm' ? 700 + text.length * 5 : 250;
  await sleep(clamp(t(lead, 0.45), 150, kind === 'comment' ? 12000 : 8000));

  let i = 0;
  let skipTypo = false;
  while (i < text.length) {
    const ch = text[i] as string;
    const isLetter = /[a-z]/i.test(ch);

    // long/unusual word coming up: a beat of hesitation before starting it
    if ((prev === ' ' || prev === '') && kind !== 'field') {
      const wordLen = (/^\S+/.exec(text.slice(i)) ?? [''])[0].length;
      if (wordLen >= 8 && Math.random() < 0.45) await sleep(clamp(t(rand(150, 440), 0.4), 80, 1200));
    }

    if (kind !== 'field' && !skipTypo && isLetter && Math.random() < persona.typoRate * (fast ? 1.7 : 0.7)) {
      const next = text[i + 1];
      const r = Math.random();
      let kindOf: 'neighbour' | 'swap' | 'double' | 'skip';
      let wrong: string;
      let consumed = 1;
      if (r < 0.2 && next && /[a-z]/i.test(next) && next !== ch) { kindOf = 'swap'; wrong = next + ch; consumed = 2; }
      else if (r < 0.35) { kindOf = 'double'; wrong = ch + ch; }
      else if (r < 0.5) { kindOf = 'skip'; wrong = ''; }
      else { kindOf = 'neighbour'; wrong = wrongKey(ch); }
      typoLog.push(kindOf);

      for (const w of wrong) await keystroke(w, kindOf === 'swap' ? 0.8 : 1);
      const rest = text.slice(i + consumed);
      // noticed immediately, or only after typing a few more characters
      const k = Math.min(rest.length, Math.random() < 0.5 ? Math.floor(rand(0, 2)) : Math.floor(rand(2, 7)));
      for (let j = 0; j < k; j++) await keystroke(rest[j] as string);
      await sleep(clamp(t(rand(240, 720), 0.4), 140, 2200)); // the "wait, that's wrong" beat
      await backspaces(wrong.length + k);
      await sleep(clamp(t(130, 0.4), 50, 500));
      skipTypo = true;
      continue;
    }
    skipTypo = false;
    await keystroke(ch);
    i++;
  }
  if (isInput) el.dispatchEvent(new Event('change', { bubbles: true }));
}

export const editorText = (el: HTMLElement) =>
  el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement ? el.value : el.innerText ?? el.textContent ?? '';
