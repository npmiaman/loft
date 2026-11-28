import { useEffect, useState } from 'react';
import { setupSteps } from '../../../lib/panel';
import { dmCap } from '../../../lib/safety';
import { DEFAULT_SETTINGS } from '../../../lib/storage';
import { useStore } from '../../../lib/useStore';
import { send } from '../ui';
import { KeyField, NumField } from './SettingsTab';

/** Step-by-step first run on Home: AI key, product, subreddits, safety limits + Reddit username. */
export default function FirstRun({ goto }: { goto: (t: string) => void }) {
  const [s, setS] = useStore('settings', DEFAULT_SETTINGS);
  const [product] = useStore('product', null);
  const [subs] = useStore('subreddits', []);
  const [onboarded, setOnboarded] = useStore('onboarded', false);
  const [me, setMe] = useState<string | null>(null); // null = checking, '' = not logged in / unknown
  const steps = setupSteps({ ...DEFAULT_SETTINGS, ...s }, product, subs, onboarded || !!s.myUsername);
  const current = steps.find((x) => !x.done)?.id ?? (onboarded ? null : 'safety');

  useEffect(() => {
    if (current !== 'safety') return;
    let alive = true;
    send<{ name: string }>({ type: 'reddit:me' }).then((r) => alive && setMe(r.ok ? r.name : '')).catch(() => alive && setMe(''));
    return () => { alive = false; };
  }, [current]);

  if (!current) return null;
  const cap = dmCap({ ...DEFAULT_SETTINGS, ...s }, null, null);

  return (
    <div className="card">
      <h3>Get Pigeon ready <span className="mut">({steps.filter((x) => x.done).length} of 4 done)</span></h3>
      <ol className="steps">
        {steps.map((st, i) => (
          <li key={st.id} className={st.id === current ? 'now' : st.done ? 'done' : ''} aria-current={st.id === current ? 'step' : undefined}>
            <div className="row">
              <span className="num" aria-hidden="true">{st.done ? '✓' : i + 1}</span>
              <b className="grow">{st.label}</b>
              {st.done && st.id !== current && <span className="mut small">Done</span>}
            </div>
            {st.id === current && st.id === 'key' && (
              <div className="mt-8">
                <div className="mut small mb-8">Pigeon uses DeepSeek to read posts and write drafts. You pay DeepSeek directly; a day of use usually costs cents.</div>
                <KeyField onDone={() => {}} />
              </div>
            )}
            {st.id === current && st.id === 'product' && (
              <div className="mt-8">
                <div className="mut small mb-8">Give Pigeon your website. It works out who your ideal customer is and how they talk.</div>
                <button className="btn pri" onClick={() => goto('setup')}>Open Setup</button>
              </div>
            )}
            {st.id === current && st.id === 'subreddits' && (
              <div className="mt-8">
                <div className="mut small mb-8">Pigeon checks which subreddits your customers post in and whether their rules allow outreach.</div>
                <button className="btn pri" onClick={() => goto('setup')}>Find subreddits</button>
              </div>
            )}
            {st.id === current && st.id === 'safety' && (
              <div className="mt-8">
                <div className="small mb-8">
                  {me === null && <span className="mut">Checking your Reddit login…</span>}
                  {me && <span className="ok"><b>Reddit: logged in as u/{me} ✓</b></span>}
                  {me === '' && <span className="err">Couldn't see a Reddit login. Log in to reddit.com in this browser, then reopen the panel.</span>}
                </div>
                {me && s.myUsername.toLowerCase() !== me.toLowerCase() && (
                  <button className="btn sm mb-8" onClick={() => setS({ ...s, myUsername: me })}>Use u/{me}</button>
                )}
                <label className="f">Your Reddit username
                  <span className="h">So Pigeon never scores or messages you.</span>
                  <input type="text" value={s.myUsername} onChange={(e) => setS({ ...s, myUsername: e.target.value.replace(/^u\//i, '').trim() })} />
                </label>
                <NumField label="Max DMs per day" hint={`With the DM ramp on, today's limit is ${cap.cap}.`} value={s.dailyDmCap} min={1} int onSave={(n) => setS({ ...s, dailyDmCap: n })} />
                <div className="mut small mb-8">Every DM, comment and post waits for your approval. Pigeon only works inside your active hours ({s.activeStart}:00 to {s.activeEnd}:00) and stops on rate limits or captchas. Change any of this under Settings.</div>
                <button className="btn pri" disabled={!s.myUsername} onClick={() => setOnboarded(true)}>Looks good</button>
              </div>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}
