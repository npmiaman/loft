import { useEffect, useState } from 'react';
import { setupSteps } from '../../../lib/panel';
import { dmBlock, dmCap, startSummary } from '../../../lib/safety';
import { DEFAULT_SETTINGS, EMPTY_AGENT, EMPTY_WARMUP } from '../../../lib/storage';
import { useLeads, useStore } from '../../../lib/useStore';
import type { Daily, RunMode } from '../../../lib/types';
import { Notice, send, today } from '../ui';
import FirstRun from './FirstRun';

const EMPTY_DAILY: Daily = { date: today(), count: 0, sessions: 0, nextDmAt: 0, pausedUntil: 0 };

/** Re-render every 30s so pause times and "today" stay right while the panel is open. */
function useNow(ms = 30_000) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}

export default function RunTab({ goto }: { goto: (t: string) => void }) {
  const [agent] = useStore('agent', EMPTY_AGENT);
  const [log] = useStore('log', []);
  const [settings] = useStore('settings', DEFAULT_SETTINGS);
  const [daily] = useStore('daily', EMPTY_DAILY);
  const [warmup] = useStore('warmup', EMPTY_WARMUP);
  const [wd] = useStore('warmupDaily', null);
  const [product] = useStore('product', null);
  const [subs] = useStore('subreddits', []);
  const leads = useLeads();
  const now = useNow();
  const [mode, setMode] = useState<RunMode>('full');
  const [msg, setMsg] = useState<{ kind: 'error' | 'info' | 'success'; text: string; at: 'run' | 'page' } | null>(null);
  const [skip, setSkip] = useState(false);

  const sentToday = daily.date === today() ? daily.count : 0;
  const cap = dmCap({ ...DEFAULT_SETTINGS, ...settings }, warmup, wd?.karma ?? null, now);
  const block = dmBlock({ ...DEFAULT_SETTINGS, ...settings }, { ...daily, count: sentToday }, cap, now);
  const ready = leads.filter((l) => l.status === 'ready' && l.message).length;
  const [onboarded] = useStore('onboarded', false);
  const steps = setupSteps({ ...DEFAULT_SETTINGS, ...settings }, product, subs, onboarded || !!settings.myUsername);
  // Start only needs key, product and subreddits; the safety step is advice, not a blocker.
  const blocker = steps.slice(0, 3).find((x) => !x.done);
  const needs = mode === 'warmup' ? (!settings.deepseekKey ? steps[0] : !warmup.plan ? { id: 'plan', label: 'Build your warm-up plan' } : null) : mode === 'dm' ? null : blocker ?? null;
  const fixTab = (id: string) => (id === 'key' ? 'settings' : id === 'plan' ? 'warmup' : 'setup');

  const start = async () => {
    setMsg(null);
    const r = await send({ type: 'agent:start', mode });
    if (!r.ok) setMsg({ kind: 'error', text: r.error ?? 'Could not start', at: 'run' });
  };
  const scan = async () => {
    setMsg({ kind: 'info', text: 'Scanning the active Reddit tab…', at: 'page' });
    const r = await send<{ added: number }>({ type: 'page:scan', skipScoring: skip });
    setMsg(r.ok ? { kind: 'success', text: `Queued ${r.added} people from this page. Their messages wait for your approval.`, at: 'page' } : { kind: 'error', text: r.error ?? 'Scan failed', at: 'page' });
  };
  const replies = async () => {
    setMsg({ kind: 'info', text: 'Checking chats…', at: 'page' });
    const r = await send<{ replies: number }>({ type: 'replies:check' });
    setMsg(r.ok ? { kind: 'success', text: `${r.replies} new repl${r.replies === 1 ? 'y' : 'ies'} found.`, at: 'page' } : { kind: 'error', text: r.error ?? 'Failed', at: 'page' });
  };

  return (
    <>
      {block && (block.kind === 'paused' || block.kind === 'plan') && (
        <div className={`banner ${block.kind}`} role="status">
          <span className="grow">{block.text}</span>
          {block.canResume && <button className="btn sm" onClick={() => send({ type: 'dm:resume' })}>Resume now</button>}
          {block.kind === 'plan' && <button className="btn sm" onClick={() => goto('warmup')}>Open plan</button>}
        </div>
      )}

      {!(onboarded && steps.every((x) => x.done)) && <FirstRun goto={goto} />}

      <div className="card run-card">
        <h3>Run the agent</h3>
        <div className="seg mb-8">
          {([['full', 'Autopilot'], ['find', 'Find leads'], ['dm', 'Send DMs'], ['warmup', 'Warm-up plan']] as [RunMode, string][]).map(([m, l]) => (
            <button key={m} className={mode === m ? 'on' : ''} aria-pressed={mode === m} onClick={() => setMode(m)}>{l}</button>
          ))}
        </div>
        <div className="mut small mb-8">
          {mode === 'full' && 'Browses your subreddits like a person, scores everyone it reads, upvotes relevant posts and writes DM drafts. Only messages you approved are sent, within your daily limit.'}
          {mode === 'find' && 'Browses and scores only. Nothing is sent. Review leads in Review.'}
          {mode === 'dm' && 'Only sends messages you already approved.'}
          {mode === 'warmup' && 'Runs today\'s part of your warm-up plan: reading, upvotes, joins, comments and posts. Build the plan in Warm-up.'}
        </div>
        <div className="row">
          {!agent.running ? (
            <button className="btn pri grow" disabled={!!needs} onClick={start}>Start</button>
          ) : (
            <button className="btn danger solid grow" onClick={() => send({ type: 'agent:stop' })}>Stop</button>
          )}
        </div>
        {!agent.running && needs && (
          <div className="notice warn start-needs">
            <span className="grow">Start needs: {needs.label}</span>
            <button className="btn sm" onClick={() => goto(fixTab(needs.id))}>Fix</button>
          </div>
        )}
        {!agent.running && !needs && <div className="small run-summary mt-8">{startSummary(mode, { cap: cap.cap, sent: sentToday, ready, block })}</div>}
        {agent.running && (
          <div className="small mut mt-8">
            {agent.phase}{agent.subreddit && ` · r/${agent.subreddit}`}
          </div>
        )}
        {msg?.at === 'run' && <Notice kind={msg.kind} onClose={() => setMsg(null)}>{msg.text}</Notice>}
        {!agent.running && agent.lastError && <Notice kind="error">Last session stopped: {agent.lastError}</Notice>}
        <div className="mut small mt-8">
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
        <div className="row small dm-cap mt-12 mb-4">
          <b>DMs today</b><span className="grow" /><span>{sentToday} / {cap.cap} · {cap.reason}</span>
        </div>
        <div className="meter"><i style={{ width: `${Math.min(100, (sentToday / Math.max(1, cap.cap)) * 100)}%` }} /></div>
        {block && (block.kind === 'cap' || block.kind === 'hours') && <div className="mut small mt-4">{block.text}</div>}
        <div className="small mut mt-8">
          Queue: {leads.filter((l) => l.status === 'queued').length} need a message · {leads.filter((l) => l.status === 'draft').length} waiting for approval · {ready} approved · {leads.filter((l) => l.status === 'sent').length} sent · {leads.filter((l) => l.status === 'replied').length} replied
        </div>
      </div>

      <div className="card">
        <h3>Page DMer</h3>
        <div className="mut small mb-8">Open any subreddit, post or search page in your browser, then queue everyone on it. Also available by right-clicking the page. Every message waits for your approval.</div>
        <label className="toggle small">
          <input type="checkbox" checked={skip} onChange={(e) => setSkip(e.target.checked)} /> <span>Skip AI scoring (queue everyone)</span>
        </label>
        <div className="row">
          <button className="btn pri" onClick={scan}>Queue everyone on this page</button>
          <button className="btn" onClick={replies}>Check replies</button>
        </div>
        {msg?.at === 'page' && <Notice kind={msg.kind} onClose={() => setMsg(null)}>{msg.text}</Notice>}
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
