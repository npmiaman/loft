import { useState } from 'react';
import { effectiveCap } from '../../../lib/governor';
import { DEFAULT_SETTINGS, EMPTY_AGENT } from '../../../lib/storage';
import { useLeads, useStore } from '../../../lib/useStore';
import type { Daily, RunMode } from '../../../lib/types';
import { send, today } from '../ui';

const EMPTY_DAILY: Daily = { date: today(), count: 0, sessions: 0, nextDmAt: 0, pausedUntil: 0 };

export default function RunTab({ goto }: { goto: (t: string) => void }) {
  const [agent] = useStore('agent', EMPTY_AGENT);
  const [log] = useStore('log', []);
  const [settings] = useStore('settings', DEFAULT_SETTINGS);
  const [daily] = useStore('daily', EMPTY_DAILY);
  const [product] = useStore('product', null);
  const [subs] = useStore('subreddits', []);
  const leads = useLeads();
  const [mode, setMode] = useState<RunMode>('full');
  const [msg, setMsg] = useState('');
  const [skip, setSkip] = useState(true);

  const sentToday = daily.date === today() ? daily.count : 0;
  const cap = effectiveCap(settings);
  const missing: [string, string][] = [];
  if (!settings.deepseekKey) missing.push(['Add your DeepSeek API key', 'settings']);
  if (!product) missing.push(['Analyze your product', 'setup']);
  else if (!subs.some((s) => s.enabled)) missing.push(['Find & enable subreddits', 'setup']);

  const start = async () => {
    setMsg('');
    const r = await send({ type: 'agent:start', mode });
    if (!r.ok) setMsg(r.error ?? 'Could not start');
  };
  const scan = async () => {
    setMsg('Scanning the active Reddit tab…');
    const r = await send<{ added: number }>({ type: 'page:scan', skipScoring: skip });
    setMsg(r.ok ? `Queued ${r.added} people from this page.` : r.error ?? 'Scan failed');
  };
  const replies = async () => {
    setMsg('Checking chats…');
    const r = await send<{ replies: number }>({ type: 'replies:check' });
    setMsg(r.ok ? `${r.replies} new repl${r.replies === 1 ? 'y' : 'ies'} found.` : r.error ?? 'Failed');
  };

  return (
    <>
      {missing.length > 0 && (
        <div className="chk">
          <b>Finish setup to run:</b>
          <ul>
            {missing.map(([t, tab]) => (
              <li key={t}>
                <a href="#" onClick={(e) => (e.preventDefault(), goto(tab))}>{t}</a>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="card">
        <h3>Run the agent</h3>
        <div className="seg" style={{ marginBottom: 10 }}>
          {([['full', 'Autopilot'], ['find', 'Find leads'], ['dm', 'Send DMs']] as [RunMode, string][]).map(([m, l]) => (
            <button key={m} className={mode === m ? 'on' : ''} onClick={() => setMode(m)}>{l}</button>
          ))}
        </div>
        <div className="mut small" style={{ marginBottom: 10 }}>
          {mode === 'full' && 'Browses your subreddits like a person, scores everyone it reads, upvotes the relevant ones and DMs within your daily cap.'}
          {mode === 'find' && 'Browses and scores only. Nothing is sent. Review leads in the Leads tab.'}
          {mode === 'dm' && 'Only sends messages that are already approved and ready.'}
        </div>
        <div className="row">
          {!agent.running ? (
            <button className="btn pri grow" disabled={missing.length > 0 && mode !== 'dm'} onClick={start}>▶ Start</button>
          ) : (
            <button className="btn danger grow" onClick={() => send({ type: 'agent:stop' })}>■ Stop</button>
          )}
        </div>
        {agent.running && (
          <div className="small mut" style={{ marginTop: 8 }}>
            {agent.phase}{agent.subreddit && ` · r/${agent.subreddit}`}
          </div>
        )}
        {msg && <div className="small" style={{ marginTop: 8 }}>{msg}</div>}
        <div className="mut small" style={{ marginTop: 8 }}>
          Pigeon works in its own Reddit window using your logged-in session. Keep that window open and visible; don't minimize it.
        </div>
      </div>

      <div className="card">
        <h3>This session</h3>
        <div className="stats">
          <div><b>{agent.stats.scanned}</b><span>scanned</span></div>
          <div><b>{agent.stats.leads}</b><span>leads</span></div>
          <div><b>{agent.stats.upvotes}</b><span>upvotes</span></div>
          <div><b>{agent.stats.dms}</b><span>DMs</span></div>
          <div><b>{agent.stats.errors}</b><span>errors</span></div>
        </div>
        <div className="row small" style={{ margin: '12px 0 4px' }}>
          <b>DMs today</b><span className="grow" /><span>{sentToday} / {cap}{settings.warmup && cap < settings.dailyDmCap ? ' (warm-up)' : ''}</span>
        </div>
        <div className="meter"><i style={{ width: `${Math.min(100, (sentToday / Math.max(1, cap)) * 100)}%` }} /></div>
        {daily.pausedUntil > Date.now() && <div className="err small" style={{ marginTop: 6 }}>DMs paused until {new Date(daily.pausedUntil).toLocaleTimeString()}</div>}
        <div className="small mut" style={{ marginTop: 8 }}>
          Queue: {leads.filter((l) => l.status === 'queued').length} found · {leads.filter((l) => l.status === 'draft').length} awaiting approval · {leads.filter((l) => l.status === 'ready').length} ready · {leads.filter((l) => l.status === 'sent').length} sent · {leads.filter((l) => l.status === 'replied').length} replied
        </div>
      </div>

      <div className="card">
        <h3>Page DMer</h3>
        <div className="mut small" style={{ marginBottom: 8 }}>Open any subreddit, post or search page in your browser, then queue everyone on it. Also available by right-clicking the page.</div>
        <label className="row small" style={{ marginBottom: 8 }}>
          <input type="checkbox" checked={skip} onChange={(e) => setSkip(e.target.checked)} /> Skip AI scoring (queue everyone)
        </label>
        <div className="row">
          <button className="btn navy" onClick={scan}>Queue everyone on this page</button>
          <button className="btn" onClick={replies}>Check replies</button>
        </div>
      </div>

      <div className="card">
        <h3>Activity</h3>
        <div className="log">
          {log.length === 0 && <span className="mut">Nothing yet.</span>}
          {[...log].reverse().slice(0, 80).map((l, i) => (
            <div key={i} className={`l-${l.level}`}><time>{new Date(l.t).toLocaleTimeString()}</time>{l.msg}</div>
          ))}
        </div>
      </div>
    </>
  );
}
