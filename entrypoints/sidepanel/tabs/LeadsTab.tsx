import { useState } from 'react';
import { renderTemplate, writeDm } from '../../../lib/brain';
import { clearLeads, DEFAULT_SETTINGS, DEFAULT_TEMPLATE, getLead, patchLead, putLead } from '../../../lib/storage';
import { useLeads, useStore } from '../../../lib/useStore';
import type { Lead, LeadStatus } from '../../../lib/types';
import { pillFor, send } from '../ui';

const FILTERS: (LeadStatus | 'all')[] = ['all', 'queued', 'draft', 'ready', 'sent', 'replied', 'failed', 'skipped'];

export default function LeadsTab() {
  const leads = useLeads();
  const [settings] = useStore('settings', DEFAULT_SETTINGS);
  const [product] = useStore('product', null);
  const [templates] = useStore('templates', [DEFAULT_TEMPLATE]);
  const [filter, setFilter] = useState<LeadStatus | 'all'>('all');
  const [edit, setEdit] = useState<Record<string, string>>({});
  const [csv, setCsv] = useState('');
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState('');

  const shown = leads.filter((l) => filter === 'all' || l.status === filter);

  const write = async (l: Lead) => {
    setBusy(l.username);
    try {
      const useAi = settings.dmMode === 'ai' && !!product && !!l.snippet;
      const message = useAi ? await writeDm(product!, l, settings) : renderTemplate((templates[0] ?? DEFAULT_TEMPLATE).body, l);
      await patchLead(l.username, { message, status: useAi && settings.approveAiDrafts ? 'draft' : 'ready' });
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy('');
    }
  };

  const saveEdit = async (l: Lead) => {
    const text = edit[l.username];
    if (text == null) return;
    await patchLead(l.username, { message: text, status: l.status === 'queued' ? 'draft' : l.status });
    setEdit(({ [l.username]: _, ...rest }) => rest);
  };

  const sendNow = async (l: Lead) => {
    setBusy(l.username);
    setMsg('');
    const r = await send({ type: 'lead:send', username: l.username });
    if (!r.ok) setMsg(r.error ?? 'Failed');
    setBusy('');
  };

  const importCsv = async () => {
    let n = 0;
    for (const raw of csv.split(/\n/)) {
      const name = (raw.split(',')[0] ?? '').trim().replace(/^.*\/user\//i, '').replace(/^\/?u\//i, '').replace(/\/$/, '');
      if (!/^[A-Za-z0-9_-]{3,20}$/.test(name) || (await getLead(name))) continue;
      await putLead({ username: name, subreddit: '', postUrl: '', postTitle: '', snippet: '', fit: 50, intent: 'imported', reason: 'Imported from list', status: 'queued', source: 'csv', createdAt: Date.now() });
      n++;
    }
    setMsg(`Imported ${n} usernames.`);
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
    for (const l of leads.filter((x) => x.status === 'draft' && x.message)) await patchLead(l.username, { status: 'ready' });
  };

  return (
    <>
      <div className="card">
        <div className="row">
          <select value={filter} onChange={(e) => setFilter(e.target.value as LeadStatus | 'all')} style={{ width: 'auto' }}>
            {FILTERS.map((f) => <option key={f} value={f}>{f} {f === 'all' ? `(${leads.length})` : `(${leads.filter((l) => l.status === f).length})`}</option>)}
          </select>
          <span className="grow" />
          <button className="btn sm" onClick={approveAll}>Approve all drafts</button>
          <button className="btn sm" onClick={exportCsv}>Export CSV</button>
        </div>
        {msg && <div className="small err" style={{ marginTop: 8 }}>{msg}</div>}
      </div>

      <div className="card">
        {shown.length === 0 && <div className="mut">No leads here yet. Run the agent in “Find leads” mode, or queue a page / import a list.</div>}
        {shown.slice(0, 100).map((l) => {
          const editing = edit[l.username] != null;
          return (
            <div className="item" key={l.username}>
              <div className="row">
                <a className="t" href={`https://www.reddit.com/user/${l.username}`} target="_blank" rel="noreferrer">u/{l.username}</a>
                <span className={`pill ${pillFor(l.fit)}`}>{l.fit}</span>
                <span className={`pill st-${l.status}`}>{l.status}</span>
                {l.subreddit && <span className="mut small">r/{l.subreddit}</span>}
                <span className="grow" />
                {l.postUrl && <a className="small" href={'https://www.reddit.com' + l.postUrl} target="_blank" rel="noreferrer">post ↗</a>}
              </div>
              <div className="why small">{l.reason}{l.error && <span className="err"> · {l.error}</span>}</div>
              {(l.message || editing) && (
                <textarea
                  value={editing ? edit[l.username] : l.message}
                  onChange={(e) => setEdit({ ...edit, [l.username]: e.target.value })}
                  disabled={l.status === 'sent' || l.status === 'replied'}
                />
              )}
              <div className="row">
                {editing && <button className="btn sm navy" onClick={() => saveEdit(l)}>Save</button>}
                {(l.status === 'queued' || l.status === 'failed') && <button className="btn sm" disabled={busy === l.username} onClick={() => write(l)}>{busy === l.username ? '…' : 'Write message'}</button>}
                {l.status === 'draft' && !editing && <button className="btn sm navy" onClick={() => patchLead(l.username, { status: 'ready' })}>Approve</button>}
                {(l.status === 'draft' || l.status === 'ready') && <button className="btn sm pri" disabled={!!busy} onClick={() => sendNow(l)}>Send now</button>}
                {l.status === 'sent' && <button className="btn sm" onClick={() => patchLead(l.username, { status: 'replied' })}>Mark replied</button>}
                {l.status !== 'sent' && l.status !== 'replied' && l.status !== 'skipped' && <button className="btn sm" onClick={() => patchLead(l.username, { status: 'skipped' })}>Skip</button>}
              </div>
            </div>
          );
        })}
      </div>

      <div className="card">
        <h3>List DMer</h3>
        <div className="mut small" style={{ marginBottom: 6 }}>Paste usernames, one per line (or a CSV with usernames in the first column).</div>
        <textarea value={csv} onChange={(e) => setCsv(e.target.value)} placeholder={'spez\nu/example_user'} />
        <div className="row" style={{ marginTop: 8 }}>
          <button className="btn navy" disabled={!csv.trim()} onClick={importCsv}>Import to queue</button>
          <span className="grow" />
          <button className="btn danger sm" onClick={() => confirm('Delete ALL leads?') && clearLeads()}>Clear all leads</button>
        </div>
      </div>
    </>
  );
}
