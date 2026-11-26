import { AbortError, clamp, frame, gauss, isAborted, lognormal, rand, sleep, smoothScrollBy } from './human';
import { persona, t, tempo } from './persona';

const CSS = `
:host{all:initial}
.cur{position:fixed;left:0;top:0;z-index:2147483647;pointer-events:none;will-change:transform;display:none}
.cur svg{display:block;filter:drop-shadow(0 2px 3px rgba(0,0,0,.35))}
.chip{position:absolute;left:18px;top:20px;background:#db2777;color:#fff;font:600 11px/1 system-ui,sans-serif;padding:4px 7px;border-radius:999px;white-space:nowrap;box-shadow:0 2px 6px rgba(0,0,0,.25)}
.rip{position:fixed;z-index:2147483646;pointer-events:none;width:14px;height:14px;margin:-7px 0 0 -7px;border-radius:50%;border:2px solid #db2777;animation:rip .55s ease-out forwards}
@keyframes rip{from{transform:scale(.4);opacity:.9}to{transform:scale(2.6);opacity:0}}
.hud{position:fixed;right:16px;bottom:16px;z-index:2147483647;display:none;align-items:center;gap:10px;background:#1C274C;color:#fff;font:500 13px/1.2 system-ui,sans-serif;padding:9px 10px 9px 14px;border-radius:999px;box-shadow:0 6px 24px rgba(0,0,0,.35);pointer-events:auto}
.hud b{color:#f9a8d4}
.hud button{all:unset;cursor:pointer;background:#db2777;color:#fff;font:700 12px system-ui,sans-serif;padding:6px 12px;border-radius:999px}
.hud button:hover{background:#be185d}
`;

function deepFromPoint(x: number, y: number): Element | null {
  let el = document.elementFromPoint(x, y);
  while (el?.shadowRoot) {
    const inner = el.shadowRoot.elementFromPoint(x, y);
    if (!inner || inner === el) break;
    el = inner;
  }
  return el;
}

type P = { x: number; y: number };
const cr = (a: number, b: number, c: number, d: number, t: number) =>
  0.5 * (2 * b + (-a + c) * t + (2 * a - 5 * b + 4 * c - d) * t * t + (-a + 3 * b - 3 * c + d) * t * t * t);

/**
 * An irregular route from A to B: a spline through several randomly displaced waypoints (lateral wander that
 * drifts rather than jumps, plus along-track jitter). Every move gets a different shape.
 */
function makePath(x0: number, y0: number, tx: number, ty: number): (u: number) => P {
  const dx = tx - x0, dy = ty - y0;
  const dist = Math.hypot(dx, dy) || 1;
  const ux = dx / dist, uy = dy / dist, nx = -uy, ny = ux;
  let n = clamp(Math.round(dist / rand(150, 320)) + (Math.random() < 0.6 ? 1 : 0), 0, 5);
  if (dist > 24) n = Math.max(n, 1);
  const fr = Array.from({ length: n }, () => rand(0.1, 0.9)).sort((a, b) => a - b);
  const pts: P[] = [{ x: x0, y: y0 }];
  let lat = gauss(0, dist * 0.035);
  for (const f of fr) {
    lat = 0.55 * lat + gauss(0, dist * rand(0.025, 0.07));
    const al = gauss(0, dist * 0.025);
    pts.push({ x: x0 + dx * f + nx * lat + ux * al, y: y0 + dy * f + ny * lat + uy * al });
  }
  pts.push({ x: tx, y: ty });
  const m = pts.length;
  return (u: number) => {
    const sc = clamp(u, 0, 1) * (m - 1);
    const i = Math.min(m - 2, Math.floor(sc));
    const f = sc - i;
    const p0 = pts[Math.max(0, i - 1)]!, p1 = pts[i]!, p2 = pts[i + 1]!, p3 = pts[Math.min(m - 1, i + 2)]!;
    return { x: cr(p0.x, p1.x, p2.x, p3.x, f), y: cr(p0.y, p1.y, p2.y, p3.y, f) };
  };
}

const UNSAFE =
  'a,button,input,textarea,select,summary,label,video,iframe,[role="button"],[role="link"],[role="menuitem"],[role="tab"],[contenteditable],[tabindex],shreddit-post,shreddit-comment,faceplate-tracker,faceplate-partial,faceplate-dropdown-menu,reddit-header-large,header,nav';
/** True if the element, or anything it's nested in (crossing shadow roots), is interactive. */
function unsafeTarget(el: Element | null): boolean {
  let cur: Element | null = el;
  while (cur) {
    if (cur.closest(UNSAFE)) return true;
    const root = cur.getRootNode();
    cur = root instanceof ShadowRoot ? root.host : null;
  }
  return !el || getComputedStyle(el).cursor === 'pointer';
}

export class GhostCursor {
  private host!: HTMLElement;
  private cur!: HTMLElement;
  private root!: ShadowRoot;
  private hudEl!: HTMLElement;
  private hudText!: HTMLElement;
  x = innerWidth * rand(0.3, 0.7);
  y = innerHeight * rand(0.3, 0.6);

  mount(onStop: () => void) {
    if (this.host) return;
    this.host = document.createElement('pigeon-cursor');
    this.root = this.host.attachShadow({ mode: 'closed' });
    this.root.innerHTML = `
      <style>${CSS}</style>
      <div class="cur"><svg width="22" height="26" viewBox="0 0 22 26"><path d="M2 2 L2 21 L7.2 16.4 L10.6 24 L14 22.5 L10.7 15 L18 15 Z" fill="#1C274C" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg><span class="chip">🕊 Pigeon</span></div>
      <div class="hud"><span class="t"></span><button>Stop</button></div>`;
    this.cur = this.root.querySelector('.cur') as HTMLElement;
    this.hudEl = this.root.querySelector('.hud') as HTMLElement;
    this.hudText = this.root.querySelector('.t') as HTMLElement;
    (this.root.querySelector('.hud button') as HTMLElement).addEventListener('click', onStop);
    document.documentElement.appendChild(this.host);
    this.place();
  }

  private place() {
    this.cur.style.transform = `translate(${this.x}px,${this.y}px)`;
  }
  show() {
    if (this.cur) this.cur.style.display = 'block';
  }
  hide() {
    if (this.cur) this.cur.style.display = 'none';
  }
  hud(text: string) {
    if (!this.hudEl) return;
    this.hudEl.style.display = text ? 'flex' : 'none';
    this.hudText.innerHTML = text;
    if (text) this.show();
    else this.hide();
  }

  private ripple(x: number, y: number) {
    const r = document.createElement('div');
    r.className = 'rip';
    r.style.left = x + 'px';
    r.style.top = y + 'px';
    this.root.appendChild(r);
    setTimeout(() => r.remove(), 600);
  }

  /**
   * Curved path, varied each time: different easing curve, speed, bend, an optional overshoot-and-correct on
   * far targets, and the odd hesitation mid-move.
   */
  async moveTo(tx: number, ty: number, opts: { noOvershoot?: boolean } = {}): Promise<void> {
    this.show();
    const x0 = this.x, y0 = this.y;
    const dx = tx - x0, dy = ty - y0;
    const dist = Math.hypot(dx, dy);
    if (dist < 2) return;

    if (!opts.noOvershoot && dist > 180 && Math.random() < persona.overshoot) {
      const ux = dx / dist, uy = dy / dist;
      const over = rand(7, 26);
      await this.moveTo(tx + ux * over + gauss(0, 3), ty + uy * over + gauss(0, 3), { noOvershoot: true });
      await sleep(Math.max(20, t(70, 0.5)));
      return this.moveTo(tx, ty, { noOvershoot: true });
    }

    const path = makePath(x0, y0, tx, ty);
    const dur = clamp(((260 + 140 * Math.log2(1 + dist / 24)) * lognormal(1, 0.2) * tempo()) / persona.mouseSpeed, 180, 2200);
    const p = rand(2, 4); // easing steepness differs per move
    const ease = (x: number) => (x < 0.5 ? 0.5 * Math.pow(2 * x, p) : 1 - 0.5 * Math.pow(2 * (1 - x), p));
    const pauseAt = Math.random() < persona.hesitation * (dist > 300 ? 1 : 0.4) ? rand(0.35, 0.8) : -1;
    let paused = false;

    const wobbleA = rand(0.008, 0.035), wobbleK = rand(2, 5), wobblePh = rand(0, 6.28);
    let nxr = 0, nyr = 0; // hand tremor: smoothed noise
    let start = performance.now();
    let lastHover = 0;
    for (;;) {
      if (isAborted()) throw new AbortError();
      const x = clamp((performance.now() - start) / dur, 0, 1);
      if (!paused && pauseAt > 0 && x >= pauseAt) {
        paused = true;
        const before = performance.now();
        await sleep(Math.max(40, t(160, 0.5)));
        start += performance.now() - before; // the pause doesn't count as movement time
      }
      // progress along the path isn't a clean ease: it surges and lags a little, occasionally creeping back
      const prog = clamp(ease(x) + wobbleA * Math.sin(2 * Math.PI * wobbleK * x + wobblePh) * Math.sin(Math.PI * x), 0, 1);
      const pt = path(prog);
      nxr = 0.82 * nxr + gauss(0, 0.28);
      nyr = 0.82 * nyr + gauss(0, 0.28);
      const taper = x > 0.92 ? (1 - x) / 0.08 : 1; // settle exactly on target
      this.x = pt.x + nxr * taper;
      this.y = pt.y + nyr * taper;
      this.place();
      const now = performance.now();
      if (now - lastHover > rand(40, 90)) {
        lastHover = now;
        deepFromPoint(this.x, this.y)?.dispatchEvent(
          new MouseEvent('mousemove', { bubbles: true, composed: true, clientX: this.x, clientY: this.y, view: window }),
        );
      }
      if (x >= 1) break;
      await frame();
    }
    this.x = tx;
    this.y = ty;
    this.place();
  }

  /** Scroll the element into a comfortable part of the viewport the way a person would. */
  async reveal(el: Element): Promise<void> {
    let r = el.getBoundingClientRect();
    if (r.top < 90 || r.bottom > innerHeight - 70) {
      await smoothScrollBy(r.top - innerHeight * rand(0.22, 0.52));
      await sleep(t(300, 0.5));
      r = el.getBoundingClientRect();
    }
  }

  async hover(el: Element): Promise<void> {
    await this.reveal(el);
    const r = el.getBoundingClientRect();
    const tx = clamp(r.left + r.width * rand(0.15, 0.65), r.left + 3, r.right - 3);
    const ty = clamp(r.top + r.height * rand(0.25, 0.65), r.top + 3, r.bottom - 3);
    await this.moveTo(tx, ty);
    const base = { bubbles: true, composed: true, clientX: tx, clientY: ty, view: window };
    el.dispatchEvent(new MouseEvent('mouseover', base));
    el.dispatchEvent(new MouseEvent('mouseenter', { ...base, bubbles: false }));
  }

  async click(el: Element): Promise<void> {
    await this.reveal(el);
    const r = el.getBoundingClientRect();
    // Nobody clicks dead centre. Mostly near-centre with a big spread, sometimes anywhere inside the target
    // (edges included), and wide targets get hit a little left of centre. Different every time.
    const biasX = 0.5 - Math.min(0.14, r.width / 1800);
    const roam = Math.random() < 0.22;
    const fx = roam ? rand(0.08, 0.92) : biasX + gauss(0, rand(0.13, 0.26));
    const fy = roam ? rand(0.12, 0.88) : 0.5 + gauss(0, rand(0.12, 0.22));
    const tx = clamp(r.left + r.width * fx, r.left + 2, r.right - 2);
    const ty = clamp(r.top + r.height * fy, r.top + 2, r.bottom - 2);
    await this.moveTo(tx, ty);
    if (Math.random() < persona.hesitation) {
      // re-aim: tiny correction after arriving
      await sleep(Math.max(30, t(120, 0.5)));
      await this.moveTo(clamp(tx + gauss(0, 3), r.left + 2, r.right - 2), clamp(ty + gauss(0, 2), r.top + 2, r.bottom - 2), { noOvershoot: true });
    }
    await sleep(Math.max(60, t(rand(110, 260), 0.5))); // settle before pressing; sometimes a long look first
    if (Math.random() < 0.06) await sleep(t(600, 0.6));
    const base = { bubbles: true, cancelable: true, composed: true, clientX: this.x, clientY: this.y, view: window, button: 0 };
    const ptr = { ...base, pointerId: 1, pointerType: 'mouse', isPrimary: true };
    el.dispatchEvent(new PointerEvent('pointerdown', { ...ptr, buttons: 1 }));
    el.dispatchEvent(new MouseEvent('mousedown', { ...base, buttons: 1 }));
    (el as HTMLElement).focus?.({ preventScroll: true });
    this.ripple(this.x, this.y);
    await sleep(clamp(lognormal(88, 0.38), 38, 260)); // how long the button is held
    el.dispatchEvent(new PointerEvent('pointerup', ptr));
    el.dispatchEvent(new MouseEvent('mouseup', base));
    el.dispatchEvent(new MouseEvent('click', base));
  }

  /** Rest on the page for ~ms while the hand keeps drifting a little, like someone reading. */
  async linger(ms: number): Promise<void> {
    const end = Date.now() + ms;
    while (Date.now() < end) {
      if (Math.random() < 0.55) {
        await this.moveTo(
          clamp(this.x + gauss(0, 48), 20, innerWidth - 20),
          clamp(this.y + gauss(0, 32), 80, innerHeight - 60),
          { noOvershoot: true },
        );
      }
      const left = end - Date.now();
      if (left <= 0) break;
      await sleep(Math.min(left, Math.max(60, t(rand(250, 900), 0.5))));
    }
  }

  /**
   * The odd stray click on empty page background, the way people click to focus a window or just fidget.
   * Only ever on non-interactive, non-pointer-cursor spots; returns false if it found nowhere safe.
   */
  async idleClick(): Promise<boolean> {
    for (let tries = 0; tries < 20; tries++) {
      const x = rand(40, innerWidth - 40), y = rand(110, innerHeight - 70);
      const el = deepFromPoint(x, y);
      if (!el || unsafeTarget(el)) continue;
      await this.moveTo(x, y);
      await sleep(Math.max(50, t(rand(90, 260), 0.5)));
      const base = { bubbles: true, cancelable: true, composed: true, clientX: x, clientY: y, view: window, button: 0 };
      el.dispatchEvent(new MouseEvent('mousedown', { ...base, buttons: 1 }));
      this.ripple(x, y);
      await sleep(clamp(lognormal(85, 0.38), 38, 240));
      el.dispatchEvent(new MouseEvent('mouseup', base));
      el.dispatchEvent(new MouseEvent('click', base));
      return true;
    }
    return false;
  }

  /** Small idle drift while "reading". */
  async fidget(): Promise<void> {
    await this.moveTo(clamp(this.x + rand(-80, 80), 20, innerWidth - 20), clamp(this.y + rand(-50, 50), 80, innerHeight - 60));
  }
}
