import { useRef, useState } from 'react';
import { compilePlan, MAX_SOP_CHARS, revisePlan } from '../../../lib/sop/compile';
import { ACCEPT, extractFile } from '../../../lib/sop/extract';
import { accountDay, CEILING, describeQuota, lastDay, planStatus, QUOTA_KEYS, startedAtForDay, localDate } from '../../../lib/sop/plan';
import { DEFAULT_SETTINGS, EMPTY_AGENT, EMPTY_WARMUP } from '../../../lib/storage';
import { useStore } from '../../../lib/useStore';
import type { SopDoc, WarmupPhase, WarmupPlan, WarmupQuota } from '../../../lib/types';
import { Field, Notice, Toggle, send, useConfirm } from '../ui';

const lines = (s: string) => s.split('\n').map((x) => x.trim()).filter(Boolean);
const KEEP_CHARS = 120_000;
const LABEL: Record<keyof WarmupQuota, string> = { browse: 'read min', upvote: 'upvotes', join: 'joins', comment: 'comments', post: 'posts', dm: 'DMs' };

export default function WarmupTab({ goto }: { goto: (t: string) => void }) {
  const [w, setW] = useStore('warmup', EMPTY_WARMUP);
  const [wd] = useStore('warmupDaily', null);
  const [items] = useStore('warmupItems', []);
  const [settings] = useStore('settings', DEFAULT_SETTINGS);
  const [product] = useStore('product', null);
  const [agent] = useStore('agent', EMPTY_AGENT);
  const [busy, setBusy] = useState('');
  const [errs, setErrs] = useState<string[]>([]);
  const [msg, setMsg] = useState('');
  const [extra, setExtra] = useState('');
  const [paste, setPaste] = useState('');
  const [tweak, setTweak] = useState('');
  const [over, setOver] = useState(false);
  const [open, setOpen] = useState<string>('');
  const input = useRef<HTMLInputElement>(null);

  const plan = w.plan;
  const set = (p: Partial<typeof w>) => setW({ ...w, ...p });
  const status = plan && w.startedAt ? planStatus(plan, w.startedAt, wd?.karma ?? null) : null;

  // ---------------------------------------------------------------- documents
  const addFiles = async (files: FileList | File[]) => {
    const list = [...files];
    if (!list.length) return;
    const bad: string[] = [];
    let docs = [...w.docs];
    for (const f of list) {
      try {
        setBusy(`Reading ${f.name}…`);
        const x = await extractFile(f, setBusy);
        const text = x.text.slice(0, KEEP_CHARS);
        const note = [x.note, x.text.length > KEEP_CHARS ? `Kept the first ${KEEP_CHARS.toLocaleString()} characters.` : ''].filter(Boolean).join(' ');
        docs = [...docs.filter((d) => d.name !== f.name), { id: `${Date.now()}-${docs.length}`, name: f.name, via: x.via, chars: text.length, text, addedAt: Date.now(), note: note || undefined }];
      } catch (e) {
        bad.push((e as Error).message);
      }
    }
    setErrs(bad);
    setBusy('');
    await setW({ ...w, docs });
    if (input.current) input.current.value = '';
  };

  const addPaste = () => {
    const text = paste.trim();
    if (!text) return;
    const n = w.docs.filter((d) => d.via === 'pasted').length + 1;
    set({ docs: [...w.docs, { id: String(Date.now()), name: `Pasted text ${n}`, via: 'pasted', chars: text.length, text: text.slice(0, KEEP_CHARS), addedAt: Date.now() }] });
    setPaste('');
  };

  const editDoc = (id: string, text: string) => set({ docs: w.docs.map((d) => (d.id === id ? { ...d, text, chars: text.length } : d)) });
  const total = w.docs.reduce((n, d) => n + d.chars, 0);

  // ---------------------------------------------------------------- plan
  const build = async () => {
    setErrs([]);
    setMsg('');
    try {
      setBusy('Reading your SOP and building the plan…');
      const { plan: p } = await compilePlan(w.docs, extra, product);
      await setW({ ...w, plan: p, startedAt: w.startedAt || Date.now() });
      setMsg('Plan built. Check the numbers and assumptions below before you turn it on.');
    } catch (e) {
      setErrs([(e as Error).message]);
    } finally {
      setBusy('');
    }
  };

  const revise = async () => {
    if (!plan || !tweak.trim()) return;
    try {
      setBusy('Updating the plan…');
      await setW({ ...w, plan: await revisePlan(plan, tweak) });
      setTweak('');
    } catch (e) {
      setErrs([(e as Error).message]);
    } finally {
      setBusy('');
    }
  };

  const editPlan = (p: Partial<WarmupPlan>) => plan && set({ plan: { ...plan, ...p } });
  const editPhase = (i: number, p: Partial<WarmupPhase>) => plan && editPlan({ phases: plan.phases.map((x, j) => (j === i ? { ...x, ...p } : x)) });
  const setQuota = (i: number, k: keyof WarmupQuota, v: number) => {
    const ph = plan?.phases[i];
    if (ph) editPhase(i, { quota: { ...ph.quota, [k]: Math.min(CEILING[k], Math.max(0, Math.round(v) || 0)) } });
  };

  // ---------------------------------------------------------------- running
  const run = async () => {
    setMsg('');
    const r = await send({ type: 'agent:start', mode: 'warmup' });
    if (!r.ok) setMsg(r.error ?? 'Could not start');
  };
  const pending = items.filter((i) => i.status === 'pending' || i.status === 'failed');
  const missingKey = !settings.deepseekKey;

  return (
    <>
      <div className="card">
        <h3>Warm-up plan</h3>
        <div className="mut small">
          A warm-up plan builds trust on a new Reddit account before you send DMs. Upload your SOP (standard operating procedure): the document that says how an account should be warmed up. PDF, Word, Excel, slides, text or a screenshot/photo. Pigeon reads it,
          turns it into a day-by-day plan (reading, upvotes, joins, comments, posts, DMs, per-subreddit rules) and runs it with the human cursor.
          Anything it can't do itself is listed as a manual step instead of being faked.
        </div>
        {missingKey && <Notice kind="warn" action={<button className="btn sm" onClick={() => goto('settings')}>Open Settings</button>}>Add your DeepSeek key first: it's what reads your SOP.</Notice>}
        {pending.length > 0 && <Notice kind="info" action={<button className="btn sm" onClick={() => goto('review')}>Open Review</button>}>{pending.length} warm-up draft{pending.length === 1 ? '' : 's'} waiting for your approval.</Notice>}
      </div>

      <div className="card">
        <h3>1. Your SOP</h3>
        <label
          className={`drop ${over ? 'over' : ''}`}
          onDragOver={(e) => (e.preventDefault(), setOver(true))}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => (e.preventDefault(), setOver(false), addFiles(e.dataTransfer.files))}
        >
          <input ref={input} data-testid="sop-file" type="file" multiple accept={ACCEPT} onChange={(e) => e.target.files && addFiles(e.target.files)} />
          <b>Drop files here or choose files</b>
          <div className="small">PDF · DOCX · XLSX · PPTX · TXT/MD/CSV · PNG/JPG screenshots and photos</div>
        </label>
        <div className="mut small my-8">
          Files are read in this browser. Photos and scanned PDFs go through built-in OCR (English, printed text; handwriting and blurry shots usually fail).
          Only the extracted text is sent to DeepSeek, when you build the plan.
        </div>
        {busy && <Notice kind="info">{busy}</Notice>}
        {errs.map((e, i) => <Notice key={i} kind="warn">{e}</Notice>)}

        {w.docs.map((d) => (
          <div key={d.id}>
            <div className="doc">
              <div className="grow">
                <div className="n">{d.name}</div>
                <div className="mut small">{d.via} · {d.chars.toLocaleString()} characters{d.note ? ` · ${d.note}` : ''}</div>
              </div>
              <button className="btn sm" onClick={() => setOpen(open === d.id ? '' : d.id)}>{open === d.id ? 'Hide' : 'View / fix'}</button>
              <button className="btn sm" onClick={() => set({ docs: w.docs.filter((x: SopDoc) => x.id !== d.id) })}>Remove</button>
            </div>
            {open === d.id && <textarea className="tall" aria-label={`Text of ${d.name}`} value={d.text} onChange={(e) => editDoc(d.id, e.target.value)} />}
          </div>
        ))}

        <Field label="Or paste it" hint="A Google Doc or Notion page: copy the text and paste it here.">
          <textarea value={paste} onChange={(e) => setPaste(e.target.value)} />
        </Field>
        <button className="btn sm" disabled={!paste.trim()} onClick={addPaste}>Add pasted text</button>

        <Field label="Anything the SOP doesn't say (optional)" hint='e.g. "I only have 30 minutes a day", "my account is brand new", "skip posting entirely"'>
          <textarea value={extra} onChange={(e) => setExtra(e.target.value)} />
        </Field>
        {total > MAX_SOP_CHARS && <Notice kind="warn">{total.toLocaleString()} characters is more than Pigeon reads in one go ({MAX_SOP_CHARS.toLocaleString()}); each document is shortened to fit.</Notice>}
        <button className="btn pri" disabled={!w.docs.length || !!busy || missingKey} onClick={build}>{plan ? 'Rebuild plan from SOP' : 'Build my warm-up plan'}</button>
        {plan && <div className="mut small mt-4">Rebuilding replaces the plan and any edits to it.</div>}
        {msg && <Notice kind="success" onClose={() => setMsg('')}>{msg}</Notice>}
      </div>

      {plan && (
        <>
          <div className="card">
            <h3>2. Your plan: {plan.name}</h3>
            <div className="small mb-8">{plan.summary}</div>
            {plan.warnings.length > 0 && <Notice kind="warn"><b>Check these</b><ul className="tight">{plan.warnings.map((x, i) => <li key={i}>{x}</li>)}</ul></Notice>}
            {plan.assumptions.length > 0 && <Notice kind="info"><b>What Pigeon had to assume</b><ul className="tight">{plan.assumptions.map((x, i) => <li key={i}>{x}</li>)}</ul></Notice>}

            <div className="row small my-8">
              <b>Today is day</b>
              <input className="w-64" aria-label="Account day" type="number" min={1} value={w.startedAt ? accountDay(w.startedAt) : 1} onChange={(e) => set({ startedAt: startedAtForDay(Number(e.target.value) || 1) })} />
              <span className="mut">of {lastDay(plan)}. Set it if the account is already part-way through.</span>
              <button className="btn sm" onClick={() => set({ startedAt: Date.now() })}>Restart plan from day 1</button>
            </div>

            {plan.phases.map((p, i) => {
              const now = status?.state === 'active' && status.index === i;
              return (
                <div key={i} className={`phase ${now ? 'now' : ''}`}>
                  <h4>{now ? 'Today: ' : ''}{p.name} <span className="mut">· days {p.fromDay}–{p.toDay}{p.minKarma ? ` · needs ${p.minKarma} karma` : ''}</span></h4>
                  <div className="small">{p.goal}</div>
                  {p.notes && <div className="mut small">{p.notes}</div>}
                  <div className="qgrid">
                    {QUOTA_KEYS.map((k) => (
                      <label key={k}>{LABEL[k]}
                        <input type="number" min={0} max={CEILING[k]} value={p.quota[k]} onChange={(e) => setQuota(i, k, Number(e.target.value))} />
                      </label>
                    ))}
                  </div>
                </div>
              );
            })}
            <div className="mut small">Per-day ceilings. Each day Pigeon aims for a random 60–100% of them so no two days look the same. Max per day: {describeQuota(CEILING)}.</div>
          </div>

          <div className="card">
            <h3>Subreddits, voice and rules</h3>
            {plan.subreddits.length === 0 && <div className="mut small mb-8">The SOP names no subreddits, so Pigeon warms up in the subreddits you picked in Setup.</div>}
            {plan.subreddits.map((s) => (
              <div key={s.name} className="small sub-line">
                <b>r/{s.name}</b> <span className="mut">from day {s.fromDay} · {s.actions.length ? s.actions.join(', ') : 'anything the phase allows'}</span>
                {s.notes && <div className="mut">{s.notes}</div>}
                <button className="btn sm" onClick={() => editPlan({ subreddits: plan.subreddits.filter((x) => x.name !== s.name) })}>Remove</button>
              </div>
            ))}
            <Field label="Voice" hint="How comments and posts should sound"><textarea value={plan.voice} onChange={(e) => editPlan({ voice: e.target.value })} /></Field>
            <Field label="Rules to obey" hint="One per line. Passed to every draft."><textarea value={plan.rules.join('\n')} onChange={(e) => editPlan({ rules: lines(e.target.value) })} /></Field>
            <Field label="Never do" hint="One per line"><textarea value={plan.avoid.join('\n')} onChange={(e) => editPlan({ avoid: lines(e.target.value) })} /></Field>
            <Field label="Sessions per day"><input type="number" min={1} max={6} value={plan.sessionsPerDay} onChange={(e) => editPlan({ sessionsPerDay: Math.min(6, Math.max(1, Number(e.target.value) || 1)) })} /></Field>
            {plan.manualSteps.length > 0 && (
              <Notice kind="info"><b>Do these yourself: Pigeon can't</b><ul className="tight">{plan.manualSteps.map((x, i) => <li key={i}>{x}</li>)}</ul></Notice>
            )}
            <Field label="Change the plan in plain English" hint='e.g. "no posting before day 10" or "make week one slower"'>
              <textarea value={tweak} onChange={(e) => setTweak(e.target.value)} />
            </Field>
            <button className="btn sm" disabled={!tweak.trim() || !!busy} onClick={revise}>Apply change</button>
          </div>

          <div className="card">
            <h3>3. Run it</h3>
            {status?.state === 'complete' && <Notice kind="warn">The plan covered days 1–{lastDay(plan)} and today is day {status.day}: warm-up is finished. Switch to Autopilot on Home, or restart the plan.</Notice>}
            {status?.state === 'active' && status.held && <Notice kind="warn">{status.held}</Notice>}
            {status?.state === 'active' && (
              <div className="small mb-8">
                <b>Day {status.day} · {status.phase.name}</b>: up to {describeQuota(status.phase.quota)}
                {wd && wd.date === localDate() && (
                  <div className="mt-8">
                    {(['browse', 'upvote', 'join', 'comment', 'post'] as const).filter((k) => wd.targets[k] > 0).map((k) => (
                      <div className="prog" key={k}>
                        <span>{LABEL[k]}</span>
                        <div className="meter"><i style={{ width: `${Math.min(100, (wd.done[k] / wd.targets[k]) * 100)}%` }} /></div>
                        <span>{Math.round(wd.done[k])}/{wd.targets[k]}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
            <Field label="Comments and posts">
              <select value={w.approval} onChange={(e) => set({ approval: e.target.value as 'review' | 'auto' })}>
                <option value="review">Wait for my approval in Review (recommended)</option>
                <option value="auto">Post automatically</option>
              </select>
            </Field>
            <Toggle checked={w.enabled} onChange={(v) => set({ enabled: v, startedAt: v && !w.startedAt ? Date.now() : w.startedAt })} label="Run the plan automatically" hint={`${plan.sessionsPerDay} session${plan.sessionsPerDay === 1 ? '' : 's'} a day inside your active hours while the plan is in progress. While it runs, DMs follow the plan's allowance instead of the DM ramp, and the plan controls scheduling. The browser must stay open.`} />
            <div className="row">
              {!agent.running ? (
                <button className="btn pri" disabled={missingKey || status?.state === 'complete'} onClick={run}>Run a warm-up session now</button>
              ) : (
                <button className="btn danger solid" onClick={() => send({ type: 'agent:stop' })}>Stop</button>
              )}
            </div>
          </div>
        </>
      )}

    </>
  );
}

/** Warm-up comment/post drafts waiting for approval (shown in Review). */
export function WarmupDrafts({ kind }: { kind: 'comment' | 'post' }) {
  const [items, setItems] = useStore('warmupItems', []);
  const [agent] = useStore('agent', EMPTY_AGENT);
  const [busy, setBusy] = useState('');
  const [msg, setMsg] = useState<{ id: string; text: string } | null>(null);
  const ask = useConfirm();
  const pending = items.filter((i) => i.kind === kind && (i.status === 'pending' || i.status === 'failed'));
  if (!pending.length) return null;

  const approve = async (id: string) => {
    const it = items.find((x) => x.id === id);
    if (!it) return;
    const yes = await ask({
      title: `Posting in r/${it.subreddit}`,
      body: <><div className="mut small mb-4">{it.kind === 'post' ? `New post: "${it.title ?? ''}"` : `Comment on "${it.postTitle ?? ''}"`}</div><div className="preview">{it.body}</div></>,
      confirm: it.kind === 'post' ? 'Post it' : 'Post comment',
    });
    if (!yes) return;
    setBusy(id);
    setMsg(null);
    const r = await send({ type: 'warmup:post', id });
    if (!r.ok) setMsg({ id, text: r.error ?? 'Posting failed' });
    setBusy('');
  };

  return (
    <div className="card">
      <h3>{kind === 'post' ? 'Post drafts' : 'Warm-up comment drafts'} <span className="mut">(from your warm-up plan)</span></h3>
      {pending.map((it) => (
        <div className="item" key={it.id}>
          <div className="t">{it.kind === 'post' ? 'New post' : 'Comment'} · r/{it.subreddit}</div>
          <div className="why small">{it.kind === 'comment' ? `on "${it.postTitle}"` : ''} {it.why}</div>
          {it.kind === 'post' && <input type="text" aria-label="Post title" value={it.title ?? ''} onChange={(e) => setItems(items.map((x) => (x.id === it.id ? { ...x, title: e.target.value } : x)))} />}
          <textarea aria-label={it.kind === 'post' ? 'Post body' : 'Comment text'} value={it.body} onChange={(e) => setItems(items.map((x) => (x.id === it.id ? { ...x, body: e.target.value } : x)))} />
          <div className="row">
            <button className="btn sm approve" disabled={!!busy || agent.running} onClick={() => approve(it.id)}>{busy === it.id ? 'Posting…' : 'Approve & post'}</button>
            <button className="btn sm" onClick={() => setItems(items.map((x) => (x.id === it.id ? { ...x, status: 'skipped' } : x)))}>Skip</button>
            {it.postUrl && <a className="small" href={it.postUrl} target="_blank" rel="noreferrer">Open thread</a>}
          </div>
          {it.status === 'failed' && msg?.id !== it.id && <Notice kind="error">{it.error ?? 'The last attempt failed.'}</Notice>}
          {msg?.id === it.id && <Notice kind="error" onClose={() => setMsg(null)}>{msg.text}</Notice>}
        </div>
      ))}
    </div>
  );
}
