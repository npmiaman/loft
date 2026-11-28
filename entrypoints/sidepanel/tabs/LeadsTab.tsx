import { useEffect, useState } from 'react';
import { viewLeads } from '../../../lib/panel';
import { renderTemplate, writeDm } from '../../../lib/brain';
import { WRITTEN_STATUS } from '../../../lib/safety';
import { clearLeads, DEFAULT_SETTINGS, DEFAULT_TEMPLATE, isKnown, patchLead, putLead } from '../../../lib/storage';
import { useLeads, useStore } from '../../../lib/useStore';
import type { Lead, LeadStatus } from '../../../lib/types';
import { EmptyState, FitPill, Notice, StatusPill, LEAD_STATUS, send, useConfirm } from '../ui';

const FILTERS: (LeadStatus | 'all')[] = ['all', 'queued', 'draft', 'ready', 'sent', 'replied', 'failed', 'skipped'];

/** The DM list. "only" pins it to one status (Review > Waiting shows just the drafts) and hides list tools. */
export default function LeadsTab({ only }: { only?: LeadStatus } = {}) {
  const leads = useLeads();
  const [settings] = useStore('settings', DEFAULT_SETTINGS);
  const [product] = useStore('product', null);
  const [templates] = useStore('templates', [DEFAULT_TEMPLATE]);
  const ask = useConfirm();
  const [filter, setFilter] = useState<LeadStatus | 'all'>('all');
  const [edit, setEdit] = useState<Record<string, string>>({});
  const [csv, setCsv] = useState('');
  const [note, setNote] = useState<{ kind: 'error' | 'success'; text: string; at: string } | null>(null);
  const [busy, setBusy] = useState('');
  const [q, setQ] = useState('');
  const [sort, setSort] = useState<'fit' | 'newest'>('fit');
  const [limit, setLimit] = useState(100);
  const [undo, setUndo] = useState<{ username: string; prev: LeadStatus } | null>(null);
  useEffect(() => {
    if (!undo) return;
    const t = setTimeout(() => setUndo(null), 8000);
    return () => clearTimeout(t);
  }, [undo]);

  const view = viewLeads(leads, { status: only ?? filter, q, sort, limit });
  const shown = view.shown;
  const drafts = leads.filter((x) => x.status === 'draft' && x.message);
  /** What's on screen for this lead: the unsaved edit if there is one, else the saved message. */
  const textOf = (l: Lead) => edit[l.username] ?? l.message ?? '';
  const dropEdit = (u: string) => setEdit(({ [u]: _, ...rest }) => rest);
  const skip = async (l: Lead) => {
    await patchLead(l.username, { status: 'skipped' });
    setUndo({ username: l.username, prev: l.status });
  };
  const restoreStatus = (l: Lead): LeadStatus => (l.message ? 'draft' : 'queued');
  const say = (at: string, kind: 'error' | 'success', text: string) => setNote({ at, kind, text });

  const write = async (l: Lead) => {
    setBusy(l.username);
    try {
      const useAi = settings.dmMode === 'ai' && !!product && !!l.snippet;
      const message = useAi ? await writeDm(product!, l, settings) : renderTemplate((templates[0] ?? DEFAULT_TEMPLATE).body, l);
      await patchLead(l.username, { message, status: WRITTEN_STATUS });
    } catch (e) {
      say(l.username, 'error', (e as Error).message);
    } finally {
      setBusy('');
    }
  };

  const saveEdit = async (l: Lead) => {
    const text = edit[l.username];
    if (text == null) return;
    await patchLead(l.username, { message: text, status: l.status === 'queued' ? WRITTEN_STATUS : l.status });
    dropEdit(l.username);
  };

  /** Approve exactly what's on screen (saves any pending edit first). */
  const approve = async (l: Lead) => {
    const message = textOf(l).trim();
    if (!message) return say(l.username, 'error', 'Write a message before approving.');
    await patchLead(l.username, { message, status: 'ready' });
    dropEdit(l.username);
  };

  const sendNow = async (l: Lead) => {
    const message = textOf(l).trim();
    if (!message) return say(l.username, 'error', 'Write a message first.');
    const yes = await ask({
      title: l.status === 'draft' ? `Approve and send to u/${l.username}?` : `Send to u/${l.username} now?`,
      body: <><div className="mut small mb-4">This message goes out right away:</div><div className="preview">{message}</div></>,
      confirm: 'Send',
    });
    if (!yes) return;
    setBusy(l.username);
    setNote(null);
    await patchLead(l.username, { message, status: 'ready' });
    dropEdit(l.username);
    const r = await send({ type: 'lead:send', username: l.username });
    if (!r.ok) say(l.username, 'error', r.error ?? 'Send failed');
    setBusy('');
  };

  const importCsv = async () => {
    let n = 0, known = 0;
    for (const raw of csv.split(/\n/)) {
      const name = (raw.split(',')[0] ?? '').trim().replace(/^.*\/user\//i, '').replace(/^\/?u\//i, '').replace(/\/$/, '');
      if (!/^[A-Za-z0-9_-]{3,20}$/.test(name)) continue;
      if (await isKnown(name)) { known++; continue; }
      await putLead({ username: name, subreddit: '', postUrl: '', postTitle: '', snippet: '', fit: 50, intent: 'imported', reason: 'Imported from list', status: 'queued', source: 'csv', createdAt: Date.now() });
      n++;
    }
    say('import', 'success', `Imported ${n} username${n === 1 ? '' : 's'}.${known ? ` Skipped ${known} already in your list or messaged before.` : ''}`);
    setCsv('');
  };

  const exportCsv = () => {
    const q = (s: string | number | undefined) => `"${String(s ?? '').replace(/"/g, '""')}"`;
    const rows = [['username', 'subreddit', 'fit', 'status', 'reason', 'message', 'post_url'].join(',')].concat(
      leads.map((l) => [l.username, l.subreddit, l.fit, l.status, l.reason, l.message, l.postUrl ? 'https://www.reddit.com' + l.postUrl : ''].map(q).join(',')),
    );
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([rows.join('\n')], { type: 'text/csv' }));
    a.download = 'pigeon-leads.csv';
    a.click();
  };

  const approveAll = async () => {
    const yes = await ask({
      title: `Approve ${drafts.length} draft${drafts.length === 1 ? '' : 's'}?`,
      body: `They will be sent by Autopilot or "Send DMs", within your daily limit. Drafts you edited but didn't save keep their saved text.`,
      confirm: `Approve ${drafts.length}`,
    });
    if (!yes) return;
    for (const l of drafts) await patchLead(l.username, { status: 'ready' });
  };

  const clear = async (unsentOnly: boolean) => {
    const n = unsentOnly ? leads.filter((l) => l.status !== 'sent' && l.status !== 'replied' && !l.sentAt).length : leads.length;
    const yes = await ask({
      title: unsentOnly ? `Clear ${n} unsent lead${n === 1 ? '' : 's'}?` : `Clear all ${n} leads?`,
      body: unsentOnly
        ? 'Leads you already messaged stay. Drafts and approved messages are deleted.'
        : 'Every lead and draft is deleted. People you already messaged are remembered, so they are never messaged again.',
      confirm: unsentOnly ? 'Clear unsent' : 'Clear all',
      danger: true,
    });
    if (yes) await clearLeads(unsentOnly);
  };

  return (
    <>
      {!only && (
        <div className="card">
          <div className="row">
            <input className="grow" type="search" aria-label="Search leads" placeholder="Search name, subreddit, reason" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <div className="row mt-8">
            <select aria-label="Status" value={filter} onChange={(e) => setFilter(e.target.value as LeadStatus | 'all')} className="w-auto">
              {FILTERS.map((f) => <option key={f} value={f}>{f === 'all' ? 'All' : LEAD_STATUS[f].label} ({f === 'all' ? leads.length : leads.filter((l) => l.status === f).length})</option>)}
            </select>
            <select aria-label="Sort" value={sort} onChange={(e) => setSort(e.target.value as 'fit' | 'newest')} className="w-auto">
              <option value="fit">Best fit first</option>
              <option value="newest">Newest first</option>
            </select>
          </div>
          <div className="row mt-8">
            <span className="mut small showing grow">{view.label}</span>
            <button className="btn sm" disabled={!drafts.length} onClick={approveAll}>Approve all ({drafts.length})</button>
            <button className="btn sm" onClick={exportCsv}>Export CSV</button>
          </div>
        </div>
      )}

      <div className="card">
        {only && <div className="row mb-8"><h3 className="grow">DM drafts</h3><button className="btn sm" disabled={!drafts.length} onClick={approveAll}>Approve all ({drafts.length})</button></div>}
        {shown.length === 0 && (
          <EmptyState title={q || filter !== 'all' ? 'No leads match' : 'No leads yet'}>
            {q || filter !== 'all' ? 'Try another search or status.' : 'Run the agent in "Find leads" mode, queue a Reddit page from Home, or import a list below.'}
          </EmptyState>
        )}
        {shown.map((l) => {
          const dirty = edit[l.username] != null && edit[l.username] !== (l.message ?? '');
          const locked = l.status === 'sent' || l.status === 'replied';
          return (
            <div className="item" key={l.username}>
              <div className="row">
                <a className="t" href={`https://www.reddit.com/user/${l.username}`} target="_blank" rel="noreferrer">u/{l.username}</a>
                <FitPill fit={l.fit} />
                <StatusPill status={l.status} />
                {l.subreddit && <span className="mut small">r/{l.subreddit}</span>}
                <span className="grow" />
                {l.postUrl && <a className="small" href={'https://www.reddit.com' + l.postUrl} target="_blank" rel="noreferrer">Open post</a>}
              </div>
              <div className="why small">{l.reason}{l.error && <span className="err"> · {l.error}</span>}</div>
              {(l.message || edit[l.username] != null) && (
                <textarea
                  aria-label={`Message to u/${l.username}`}
                  value={textOf(l)}
                  onChange={(e) => setEdit({ ...edit, [l.username]: e.target.value })}
                  disabled={locked}
                />
              )}
              <div className="row">
                {dirty && l.status !== 'draft' && <button className="btn sm" onClick={() => saveEdit(l)}>Save</button>}
                {(l.status === 'queued' || l.status === 'failed') && <button className="btn sm" disabled={busy === l.username} onClick={() => write(l)}>{busy === l.username ? 'Writing…' : 'Write message'}</button>}
                {l.status === 'draft' && <button className="btn sm approve" onClick={() => approve(l)}>Approve</button>}
                {l.status === 'draft' && <button className="btn sm" disabled={!!busy} onClick={() => sendNow(l)}>Approve & send</button>}
                {l.status === 'ready' && <button className="btn sm pri" disabled={!!busy} onClick={() => sendNow(l)}>{busy === l.username ? 'Sending…' : 'Send now'}</button>}
                {l.status === 'sent' && <button className="btn sm" onClick={() => patchLead(l.username, { status: 'replied' })}>Mark replied</button>}
                {!locked && l.status !== 'skipped' && <button className="btn sm" onClick={() => skip(l)}>Skip</button>}
                {l.status === 'skipped' && <button className="btn sm" onClick={() => patchLead(l.username, { status: restoreStatus(l) })}>Restore</button>}
              </div>
              {note?.at === l.username && <Notice kind={note.kind} onClose={() => setNote(null)}>{note.text}</Notice>}
            </div>
          );
        })}
        {view.matching > shown.length && <button className="btn sm mt-8" onClick={() => setLimit(limit + 100)}>Show 100 more ({view.label})</button>}
      </div>

      {undo && (
        <div className="toast" role="status">
          <span className="grow">Skipped u/{undo.username}.</span>
          <button className="btn sm" onClick={() => { patchLead(undo.username, { status: undo.prev }); setUndo(null); }}>Undo</button>
        </div>
      )}

      {!only && <div className="card">
        <h3>Import a list</h3>
        <div className="mut small mb-8">Paste usernames, one per line (or a CSV with usernames in the first column). Each one gets a draft for you to approve.</div>
        <textarea aria-label="Usernames to import" value={csv} onChange={(e) => setCsv(e.target.value)} placeholder={'spez\nu/example_user'} />
        <div className="row mt-8">
          <button className="btn pri" disabled={!csv.trim()} onClick={importCsv}>Import to queue</button>
        </div>
        {note?.at === 'import' && <Notice kind={note.kind} onClose={() => setNote(null)}>{note.text}</Notice>}
        <div className="row mt-12">
          <button className="btn danger sm" onClick={() => clear(true)}>Clear unsent leads</button>
          <button className="btn danger sm" onClick={() => clear(false)}>Clear all leads</button>
        </div>
      </div>}
    </>
  );
}
