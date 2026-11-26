import { DEFAULT_SETTINGS } from '../../../lib/storage';
import { effectiveCap } from '../../../lib/governor';
import { useStore } from '../../../lib/useStore';
import type { Settings } from '../../../lib/types';
import { Field, Toggle } from '../ui';

export default function SettingsTab() {
  const [s, setS] = useStore('settings', DEFAULT_SETTINGS);
  const set = (p: Partial<Settings>) => setS({ ...s, ...p });
  const num = (k: keyof Settings) => (e: React.ChangeEvent<HTMLInputElement>) => set({ [k]: Number(e.target.value) } as Partial<Settings>);

  return (
    <>
      <div className="card">
        <h3>DeepSeek</h3>
        <Field label="API key" hint="Stored locally in this browser. Get one at platform.deepseek.com.">
          <input type="password" value={s.deepseekKey} onChange={(e) => set({ deepseekKey: e.target.value.trim() })} placeholder="sk-..." />
        </Field>
        <Field label="Model" hint="Flash is fast and cheap; Pro is stronger for scoring and writing.">
          <select value={s.model} onChange={(e) => set({ model: e.target.value as Settings['model'] })}>
            <option value="deepseek-flash">deepseek-flash</option>
            <option value="deepseek-v4-pro">deepseek-v4-pro</option>
          </select>
        </Field>
        <Field label="Your Reddit username" hint="So the agent never scores or messages you."><input type="text" value={s.myUsername} onChange={(e) => set({ myUsername: e.target.value.replace(/^u\//i, '') })} /></Field>
      </div>

      <div className="card">
        <h3>Pacing & safety</h3>
        <Field label="Max DMs per day"><input type="number" min={1} value={s.dailyDmCap} onChange={num('dailyDmCap')} /></Field>
        <Toggle checked={s.warmup} onChange={(v) => set({ warmup: v, warmupStart: v && !s.warmupStart ? Date.now() : s.warmupStart })} label="Warm-up ramp" hint={`Starts low and climbs over ~8 days. Today's effective cap: ${effectiveCap(s)}.`} />
        {s.warmup && <button className="btn sm" onClick={() => set({ warmupStart: Date.now() })} style={{ marginBottom: 10 }}>Restart warm-up from today</button>}
        <div className="row">
          <Field label="Active from (hour)"><input type="number" min={0} max={23} value={s.activeStart} onChange={num('activeStart')} /></Field>
          <Field label="to (hour)"><input type="number" min={0} max={23} value={s.activeEnd} onChange={num('activeEnd')} /></Field>
        </div>
        <div className="row">
          <Field label="Min gap between DMs (s)"><input type="number" min={10} value={s.minGapSec} onChange={num('minGapSec')} /></Field>
          <Field label="Max gap (s)"><input type="number" min={10} value={s.maxGapSec} onChange={num('maxGapSec')} /></Field>
        </div>
        <Field label={`On-page speed: ${s.pace.toFixed(1)}×`} hint="Typing, scrolling and clicking speed. Each session also gets its own random speed and rhythm on top of this.">
          <input type="range" min={0.5} max={1.8} step={0.1} value={s.pace} onChange={num('pace')} />
        </Field>
        <div className="mut small">Pigeon stops automatically on rate-limit warnings, captchas, or three failed sends in a row.</div>
      </div>

      <div className="card">
        <h3>Brain & browsing</h3>
        <Field label={`Lead threshold: ${s.minFit}`} hint="Only people the AI scores at or above this become leads."><input type="range" min={40} max={95} value={s.minFit} onChange={num('minFit')} /></Field>
        <Field label="Session length (minutes)"><input type="number" min={5} value={s.sessionMinutes} onChange={num('sessionMinutes')} /></Field>
        <Toggle checked={s.upvotes} onChange={(v) => set({ upvotes: v })} label="Upvote relevant posts and comments" hint="Only things the agent actually read and judged relevant." />
        {s.upvotes && <Field label="Max upvotes per session"><input type="number" min={0} value={s.maxUpvotesPerSession} onChange={num('maxUpvotesPerSession')} /></Field>}
        <Toggle checked={s.autoSchedule} onChange={(v) => set({ autoSchedule: v })} label="Run sessions automatically" hint="Starts an Autopilot session during active hours. Needs the browser open." />
        {s.autoSchedule && <Field label="Sessions per day"><input type="number" min={1} value={s.sessionsPerDay} onChange={num('sessionsPerDay')} /></Field>}
      </div>
    </>
  );
}
