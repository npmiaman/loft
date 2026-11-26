import { useState } from 'react';
import { buildProfile, discoverSubreddits, scrapeSite } from '../../../lib/brain';
import { useStore } from '../../../lib/useStore';
import type { Product, Subreddit } from '../../../lib/types';
import { Field } from '../ui';

const lines = (s: string) => s.split(/\n|,/).map((x) => x.trim()).filter(Boolean);

export default function SetupTab() {
  const [product, setProduct] = useStore('product', null);
  const [subs, setSubs] = useStore('subreddits', []);
  const [url, setUrl] = useState(product?.url ?? '');
  const [extra, setExtra] = useState('');
  const [busy, setBusy] = useState('');
  const [err, setErr] = useState('');
  const [manual, setManual] = useState('');

  const analyze = async () => {
    setErr('');
    try {
      const u = /^https?:\/\//.test(url) ? url : `https://${url}`;
      setBusy('Reading your site…');
      const ok = await chrome.permissions.request({ origins: [new URL(u).origin + '/*'] });
      if (!ok) throw new Error('Permission to read your website was denied.');
      const text = await scrapeSite(u);
      setBusy('Building your ICP profile…');
      setProduct(await buildProfile(u, text, extra));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy('');
    }
  };

  const find = async () => {
    if (!product) return;
    setErr('');
    try {
      const found = await discoverSubreddits(product, setBusy);
      const old = new Map(subs.map((s) => [s.name.toLowerCase(), s]));
      const merged = found.map((s) => ({ ...s, enabled: old.get(s.name.toLowerCase())?.enabled ?? s.enabled, lastVisited: old.get(s.name.toLowerCase())?.lastVisited }));
      const keep = subs.filter((s) => !found.some((f) => f.name.toLowerCase() === s.name.toLowerCase()));
      setSubs([...merged, ...keep].sort((a, b) => b.score - a.score));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy('');
    }
  };

  const addManual = () => {
    const name = manual.replace(/^\/?r\//i, '').trim();
    if (!name || subs.some((s) => s.name.toLowerCase() === name.toLowerCase())) return;
    const s: Subreddit = { name, subscribers: 0, postsPerDay: 0, icpDensity: 0, rulesRisk: 'medium', score: 50, reason: 'Added manually', enabled: true };
    setSubs([s, ...subs]);
    setManual('');
  };

  const edit = (patch: Partial<Product>) => product && setProduct({ ...product, ...patch });

  return (
    <>
      <div className="card">
        <h3>1 · Your product</h3>
        <Field label="Website"><input type="text" placeholder="trypigeon.in" value={url} onChange={(e) => setUrl(e.target.value)} /></Field>
        <Field label="Anything the site doesn't say" hint="Who you sell to, what makes you different. Optional but it sharpens the ICP.">
          <textarea value={extra} onChange={(e) => setExtra(e.target.value)} />
        </Field>
        <button className="btn pri" disabled={!url || !!busy} onClick={analyze}>{product ? 'Re-analyze' : 'Analyze my product'}</button>
      </div>

      {product && (
        <div className="card">
          <h3>2 · ICP profile <span className="mut small">(edit anything)</span></h3>
          <Field label="Name"><input type="text" value={product.name} onChange={(e) => edit({ name: e.target.value })} /></Field>
          <Field label="What it does"><textarea value={product.summary} onChange={(e) => edit({ summary: e.target.value })} /></Field>
          <Field label="What you offer them"><input type="text" value={product.offer} onChange={(e) => edit({ offer: e.target.value })} /></Field>
          <Field label="Ideal customer"><textarea value={product.icp} onChange={(e) => edit({ icp: e.target.value })} /></Field>
          <Field label="Pain phrases" hint="How they actually talk, one per line"><textarea value={product.painPhrases.join('\n')} onChange={(e) => edit({ painPhrases: lines(e.target.value) })} /></Field>
          <Field label="Buying signals" hint="Post patterns that mean 'now', one per line"><textarea value={product.buyingSignals.join('\n')} onChange={(e) => edit({ buyingSignals: lines(e.target.value) })} /></Field>
          <Field label="Competitors" hint="Comma separated"><input type="text" value={product.competitors.join(', ')} onChange={(e) => edit({ competitors: lines(e.target.value) })} /></Field>
          <Field label="Subreddit ideas" hint="The brain verifies these against Reddit"><textarea value={product.subredditIdeas.join(', ')} onChange={(e) => edit({ subredditIdeas: lines(e.target.value) })} /></Field>
        </div>
      )}

      {product && (
        <div className="card">
          <h3>3 · Where they hang out</h3>
          <div className="mut small" style={{ marginBottom: 8 }}>
            Each subreddit is checked live: member count, posting rate, ICP density across 25 real recent titles, and whether the rules ban promotion or DMs.
          </div>
          <button className="btn pri" disabled={!!busy} onClick={find}>{subs.length ? 'Re-run discovery' : 'Find subreddits'}</button>
          <div className="row" style={{ margin: '10px 0' }}>
            <input className="grow" type="text" placeholder="add r/name manually" value={manual} onChange={(e) => setManual(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && addManual()} />
            <button className="btn" onClick={addManual}>Add</button>
          </div>
          {subs.map((s) => (
            <div className="item" key={s.name}>
              <div className="row">
                <input type="checkbox" checked={s.enabled} onChange={(e) => setSubs(subs.map((x) => (x.name === s.name ? { ...x, enabled: e.target.checked } : x)))} />
                <a className="t" href={`https://www.reddit.com/r/${s.name}`} target="_blank" rel="noreferrer">r/{s.name}</a>
                <span className={`pill ${s.score >= 70 ? 'hi' : s.score >= 50 ? 'mid' : 'lo'}`}>{s.score}</span>
                <span className={`pill ${s.rulesRisk === 'low' ? 'hi' : s.rulesRisk === 'medium' ? 'mid' : 'lo'}`}>rules: {s.rulesRisk}</span>
                <span className="grow" />
                <button className="btn sm" onClick={() => setSubs(subs.filter((x) => x.name !== s.name))}>✕</button>
              </div>
              <div className="why small">
                {s.subscribers ? `${s.subscribers.toLocaleString()} members · ${s.postsPerDay}/day · ICP ${s.icpDensity}% · ` : ''}{s.reason}
              </div>
            </div>
          ))}
        </div>
      )}

      {busy && <div className="card">⏳ {busy}</div>}
      {err && <div className="card err">{err}</div>}
    </>
  );
}
