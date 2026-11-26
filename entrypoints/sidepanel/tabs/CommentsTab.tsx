import { useState } from 'react';
import { generatePostIdeas } from '../../../lib/brain';
import { useStore } from '../../../lib/useStore';
import { send } from '../ui';

export default function CommentsTab() {
  const [opps, setOpps] = useStore('opportunities', []);
  const [ideas, setIdeas] = useStore('postIdeas', []);
  const [product] = useStore('product', null);
  const [subs] = useStore('subreddits', []);
  const [busy, setBusy] = useState('');
  const [msg, setMsg] = useState('');

  const post = async (id: string) => {
    setBusy(id);
    setMsg('');
    const r = await send({ type: 'comment:post', id });
    if (!r.ok) setMsg(r.error ?? 'Failed');
    setBusy('');
  };

  const gen = async () => {
    if (!product) return;
    setBusy('ideas');
    setMsg('');
    try {
      setIdeas(await generatePostIdeas(product, subs.filter((s) => s.enabled).slice(0, 8)));
    } catch (e) {
      setMsg((e as Error).message);
    }
    setBusy('');
  };

  const pending = opps.filter((o) => o.status === 'pending' || o.status === 'failed');

  return (
    <>
      <div className="card">
        <h3>Comment opportunities</h3>
        <div className="mut small" style={{ marginBottom: 8 }}>
          While browsing, the agent drafts replies for threads where a helpful answer fits. Nothing is posted until you approve it.
        </div>
        {pending.length === 0 && <div className="mut">None yet. Run the agent to find some.</div>}
        {pending.map((o) => (
          <div className="item" key={o.id}>
            <div className="t">{o.title}</div>
            <div className="why small">r/{o.subreddit} · u/{o.author} · {o.why}</div>
            <textarea value={o.draft} onChange={(e) => setOpps(opps.map((x) => (x.id === o.id ? { ...x, draft: e.target.value } : x)))} />
            <div className="row">
              <button className="btn sm pri" disabled={!!busy} onClick={() => post(o.id)}>{busy === o.id ? 'Posting…' : 'Approve & post'}</button>
              <button className="btn sm" onClick={() => setOpps(opps.map((x) => (x.id === o.id ? { ...x, status: 'skipped' } : x)))}>Skip</button>
              <a className="small" href={o.postUrl} target="_blank" rel="noreferrer">open ↗</a>
              {o.status === 'failed' && <span className="err small">last attempt failed</span>}
            </div>
          </div>
        ))}
        {msg && <div className="err small" style={{ marginTop: 8 }}>{msg}</div>}
      </div>

      <div className="card">
        <h3>Post ideas</h3>
        <div className="mut small" style={{ marginBottom: 8 }}>Value-first posts for your best subreddits. Copy one, check the sub's rules, and post it yourself.</div>
        <button className="btn navy" disabled={!product || busy === 'ideas'} onClick={gen}>{busy === 'ideas' ? 'Writing…' : 'Generate 5 post ideas'}</button>
        {ideas.map((i) => (
          <div className="item" key={i.id}>
            <div className="t">{i.title}</div>
            <div className="why small">r/{i.subreddit} · {i.angle}</div>
            <div className="preview">{i.body}</div>
            <div className="row" style={{ marginTop: 6 }}>
              <button className="btn sm" onClick={() => navigator.clipboard.writeText(`${i.title}\n\n${i.body}`)}>Copy</button>
              <a className="btn sm" href={`https://www.reddit.com/r/${i.subreddit}/submit`} target="_blank" rel="noreferrer">Open submit page ↗</a>
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
