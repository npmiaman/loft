import { useState } from 'react';
import { generatePostIdeas } from '../../../lib/brain';
import { useStore } from '../../../lib/useStore';
import { EmptyState, Notice, send, useConfirm } from '../ui';

/** Comment drafts the agent wrote while browsing (Review > Comments). */
export default function CommentsTab({ compact }: { compact?: boolean } = {}) {
  const [opps, setOpps] = useStore('opportunities', []);
  const [busy, setBusy] = useState('');
  const [msg, setMsg] = useState<{ id: string; text: string } | null>(null);
  const ask = useConfirm();

  const post = async (id: string) => {
    const o = opps.find((x) => x.id === id);
    if (!o) return;
    const yes = await ask({
      title: `Posting in r/${o.subreddit}`,
      body: <><div className="mut small mb-4">Reply to "{o.title}":</div><div className="preview">{o.draft}</div></>,
      confirm: 'Post comment',
    });
    if (!yes) return;
    setBusy(id);
    setMsg(null);
    const r = await send({ type: 'comment:post', id });
    if (!r.ok) setMsg({ id, text: r.error ?? 'Posting failed' });
    setBusy('');
  };

  const pending = opps.filter((o) => o.status === 'pending' || o.status === 'failed');
  if (compact && !pending.length) return null;

  return (
    <div className="card">
      <h3>Comment drafts</h3>
      {!compact && <div className="mut small mb-8">While browsing, the agent drafts replies for threads where a helpful answer fits. Nothing is posted until you approve it.</div>}
      {pending.length === 0 && <EmptyState title="No comment drafts">Run Autopilot or Find leads on Home and drafts show up here.</EmptyState>}
      {pending.map((o) => (
        <div className="item" key={o.id}>
          <div className="t">{o.title}</div>
          <div className="why small">r/{o.subreddit} · u/{o.author} · {o.why}</div>
          <textarea aria-label={`Comment draft for "${o.title}"`} value={o.draft} onChange={(e) => setOpps(opps.map((x) => (x.id === o.id ? { ...x, draft: e.target.value } : x)))} />
          <div className="row">
            <button className="btn sm approve" disabled={!!busy} onClick={() => post(o.id)}>{busy === o.id ? 'Posting…' : 'Approve & post'}</button>
            <button className="btn sm" onClick={() => setOpps(opps.map((x) => (x.id === o.id ? { ...x, status: 'skipped' } : x)))}>Skip</button>
            <a className="small" href={o.postUrl} target="_blank" rel="noreferrer">Open thread</a>
          </div>
          {o.status === 'failed' && msg?.id !== o.id && <Notice kind="error">The last attempt to post this failed.</Notice>}
          {msg?.id === o.id && <Notice kind="error" onClose={() => setMsg(null)}>{msg.text}</Notice>}
        </div>
      ))}
    </div>
  );
}

/** Post ideas you post yourself (Review > Posts). */
export function PostIdeas() {
  const [ideas, setIdeas] = useStore('postIdeas', []);
  const [product] = useStore('product', null);
  const [subs] = useStore('subreddits', []);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [copied, setCopied] = useState('');

  const gen = async () => {
    if (!product) return;
    setBusy(true);
    setErr('');
    try {
      setIdeas(await generatePostIdeas(product, subs.filter((s) => s.enabled).slice(0, 8)));
    } catch (e) {
      setErr((e as Error).message);
    }
    setBusy(false);
  };

  return (
    <div className="card">
      <h3>Post ideas</h3>
      <div className="mut small mb-8">Value-first posts for your best subreddits. Copy one, check the subreddit's rules, and post it yourself.</div>
      <button className="btn pri" disabled={!product || busy} onClick={gen}>{busy ? 'Writing…' : 'Write 5 post ideas'}</button>
      {!product && <Notice kind="info">Describe your product in Setup first.</Notice>}
      {err && <Notice kind="error" onClose={() => setErr('')}>{err}</Notice>}
      {ideas.map((i) => (
        <div className="item" key={i.id}>
          <div className="t">{i.title}</div>
          <div className="why small">r/{i.subreddit} · {i.angle}</div>
          <div className="preview">{i.body}</div>
          <div className="row mt-8">
            <button className="btn sm" onClick={() => navigator.clipboard.writeText(`${i.title}\n\n${i.body}`).then(() => setCopied(i.id))}>{copied === i.id ? 'Copied' : 'Copy'}</button>
            <a className="btn sm" href={`https://www.reddit.com/r/${i.subreddit}/submit`} target="_blank" rel="noreferrer">Open submit page</a>
          </div>
        </div>
      ))}
    </div>
  );
}
