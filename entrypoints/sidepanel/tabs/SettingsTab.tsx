import { useEffect, useState } from 'react';
import { keyCheckMessage, parseNum, validateSettings } from '../../../lib/panel';
import { dmCap } from '../../../lib/safety';
import { planStatus } from '../../../lib/sop/plan';
import { DEFAULT_SETTINGS, EMPTY_WARMUP } from '../../../lib/storage';
import { useStore } from '../../../lib/useStore';
import type { Settings } from '../../../lib/types';
import { Field, Notice, Toggle, send } from '../ui';

/** A number box that keeps what you type and only saves valid values (no 0 while the box is empty). */
export function NumField(p: {
  label: string; hint?: string; value: number; min?: number; max?: number; int?: boolean; disabled?: boolean;
  check?: (n: number) => string | undefined; onSave: (n: number) => void; rangeMsg?: string;
}) {
  const [text, setText] = useState(String(p.value));
  const [err, setErr] = useState('');
  useEffect(() => { setText(String(p.value)); setErr(''); }, [p.value]);
  const range = p.rangeMsg ?? (p.max != null ? `Use a number from ${p.min ?? 0} to ${p.max}.` : `Use a whole number of at least ${p.min ?? 0}.`);
  const change = (t: string) => {
    setText(t);
    const n = parseNum(t, { min: p.min, max: p.max, int: p.int });
    if (n == null) return setErr(t.trim() ? range : 'Enter a number.');
    const e = p.check?.(n);
    if (e) return setErr(e);
    setErr('');
    p.onSave(n);
  };
  return (
    <Field label={p.label} hint={p.hint} error={err} disabled={p.disabled}>
      <input type="number" min={p.min} max={p.max} value={text} disabled={p.disabled} aria-invalid={!!err} onChange={(e) => change(e.target.value)} onBlur={() => err && (setText(String(p.value)), setErr(''))} />
    </Field>
  );
}

export function KeyField({ onDone }: { onDone?: () => void }) {
  const [s, setS] = useStore('settings', DEFAULT_SETTINGS);
  const [res, setRes] = useState<{ kind: 'success' | 'error' | 'info'; text: string } | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const key = draft ?? s.deepseekKey;
  const test = async () => {
    setRes({ kind: 'info', text: 'Checking the key with DeepSeek…' });
    const k = key.trim();
    const r = await send<{ result: { ok: boolean; status?: number; error?: string } }>({ type: 'key:test', key: k, model: s.model });
    const m = keyCheckMessage(r.ok ? r.result : { ok: false, error: r.error });
    setRes(m);
    if (m.kind === 'success') {
      await setS({ ...s, deepseekKey: k });
      setDraft(null);
      onDone?.();
    }
  };
  return (
    <>
      <Field label="DeepSeek API key" hint="Stored only in this browser. Get one at platform.deepseek.com.">
        <input type="password" value={key} onChange={(e) => { setDraft(e.target.value.trim()); setRes(null); if (!onDone) setS({ ...s, deepseekKey: e.target.value.trim() }); }} placeholder="sk-..." />
      </Field>
      <button className="btn sm" disabled={!key.trim()} onClick={test}>Test key</button>
      {res && <Notice kind={res.kind}>{res.text}</Notice>}
    </>
  );
}

export default function SettingsTab() {
  const [s, setS] = useStore('settings', DEFAULT_SETTINGS);
  const [w] = useStore('warmup', EMPTY_WARMUP);
  const [wd] = useStore('warmupDaily', null);
  const set = (p: Partial<Settings>) => setS({ ...s, ...p });
  const cross = (k: keyof Settings) => (n: number) => {
    const e = validateSettings({ ...DEFAULT_SETTINGS, ...s, [k]: n });
    return e[k] ?? (k === 'minGapSec' && e.maxGapSec ? 'Max gap must be at least the min gap.' : k === 'activeStart' ? e.activeEnd : undefined);
  };
  const save = (k: keyof Settings) => (n: number) => set({ [k]: n } as Partial<Settings>);
  const planOn = !!(w.enabled && w.plan && w.startedAt && planStatus(w.plan, w.startedAt, wd?.karma ?? null).state === 'active');
  const cap = dmCap({ ...DEFAULT_SETTINGS, ...s }, w, wd?.karma ?? null);

  return (
    <>
      <div className="card">
        <h3>DeepSeek</h3>
        <KeyField />
        <div className="mt-12" />
        <Field label="Model" hint="Flash is fast and cheap; Pro is stronger for scoring and writing.">
          <select value={s.model} onChange={(e) => set({ model: e.target.value as Settings['model'] })}>
            <option value="deepseek-flash">deepseek-flash</option>
            <option value="deepseek-v4-pro">deepseek-v4-pro</option>
          </select>
        </Field>
        <Field label="Your Reddit username" hint="So the agent never scores or messages you."><input type="text" value={s.myUsername} onChange={(e) => set({ myUsername: e.target.value.replace(/^u\//i, '').trim() })} /></Field>
      </div>

      <div className="card">
        <h3>Pacing and safety</h3>
        <NumField label="Max DMs per day" value={s.dailyDmCap} min={1} int onSave={save('dailyDmCap')} />
        {planOn && <Notice kind="info">Controlled by your warm-up plan: today's DM limit is {cap.cap}, set by the {cap.reason}. The plan also runs its own sessions. Turn the plan off in Warm-up to use the settings below.</Notice>}
        <div className="mt-8" />
        <Toggle disabled={planOn} checked={s.warmup} onChange={(v) => set({ warmup: v, warmupStart: v && !s.warmupStart ? Date.now() : s.warmupStart })} label="DM ramp (new accounts)" hint={`Starts at 5 DMs a day and climbs to your max over about 8 days. Today's limit: ${cap.cap}, from ${cap.reason}.`} />
        {s.warmup && !planOn && <button className="btn sm mb-8" onClick={() => set({ warmupStart: Date.now() })}>Restart DM ramp from today</button>}
        <div className="row">
          <div className="grow"><NumField label="Active from (hour)" value={s.activeStart} min={0} max={23} int rangeMsg="Use an hour between 0 and 23." check={cross('activeStart')} onSave={save('activeStart')} /></div>
          <div className="grow"><NumField label="to (hour)" value={s.activeEnd} min={0} max={23} int rangeMsg="Use an hour between 0 and 23." check={cross('activeEnd')} onSave={save('activeEnd')} /></div>
        </div>
        <div className="row">
          <div className="grow"><NumField label="Min gap between DMs (s)" value={s.minGapSec} min={10} int check={cross('minGapSec')} onSave={save('minGapSec')} /></div>
          <div className="grow"><NumField label="Max gap (s)" value={s.maxGapSec} min={10} int check={cross('maxGapSec')} onSave={save('maxGapSec')} /></div>
        </div>
        <Field label={`On-page speed: ${s.pace.toFixed(1)}×`} hint="Typing, scrolling and clicking speed. Each session also gets its own random speed and rhythm on top of this.">
          <input type="range" min={0.5} max={1.8} step={0.1} value={s.pace} onChange={(e) => set({ pace: Number(e.target.value) })} />
        </Field>
        <div className="mut small">Pigeon stops on its own after a rate-limit warning, a captcha, or three failed sends in a row.</div>
      </div>

      <div className="card">
        <h3>Scoring and browsing</h3>
        <Field label={`Lead threshold: ${s.minFit}`} hint="Only people the AI scores at or above this become leads."><input type="range" min={40} max={95} value={s.minFit} onChange={(e) => set({ minFit: Number(e.target.value) })} /></Field>
        <NumField label="Session length (minutes)" value={s.sessionMinutes} min={5} int onSave={save('sessionMinutes')} />
        <Toggle checked={s.upvotes} onChange={(v) => set({ upvotes: v })} label="Upvote relevant posts and comments" hint="Only things the agent actually read and judged relevant." />
        {s.upvotes && <NumField label="Max upvotes per session" value={s.maxUpvotesPerSession} min={0} int onSave={save('maxUpvotesPerSession')} />}
        <Toggle disabled={planOn} checked={s.autoSchedule} onChange={(v) => set({ autoSchedule: v })} label="Run sessions automatically" hint={planOn ? 'Controlled by your warm-up plan while it runs.' : 'Starts an Autopilot session during active hours. Needs the browser open.'} />
        {s.autoSchedule && !planOn && <NumField label="Sessions per day" value={s.sessionsPerDay} min={1} int onSave={save('sessionsPerDay')} />}
      </div>

      <div className="card">
        <h3>Words Pigeon uses</h3>
        <dl className="gloss small">
          <dt>Lead</dt><dd>A Reddit user who fits your ideal customer and may get a DM.</dd>
          <dt>DM</dt><dd>A private Reddit chat message to a lead. Every DM waits for your approval.</dd>
          <dt>Comment draft</dt><dd>A reply Pigeon wrote for a thread. Posted only when you approve it.</dd>
          <dt>Post draft</dt><dd>A new post your warm-up plan wrote. Posted only when you approve it (unless you chose automatic posting).</dd>
          <dt>Warm-up plan</dt><dd>A day-by-day plan built from your SOP (standard operating procedure) that warms up a new account. While it runs it sets the DM limit and the schedule.</dd>
          <dt>DM ramp</dt><dd>The built-in limit for new accounts: 5 DMs a day, rising to your max over about 8 days.</dd>
        </dl>
      </div>
    </>
  );
}
