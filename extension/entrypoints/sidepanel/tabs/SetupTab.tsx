import { useState } from 'react';
import { buildProfile, discoverSubreddits, scrapeSite } from '../../../lib/brain';
import { DEFAULT_SETTINGS } from '../../../lib/storage';
import { useStore } from '../../../lib/useStore';
import type { Product, Subreddit } from '../../../lib/types';
import { Field, Notice, Pill } from '../ui';
import MessagesTab from './MessagesTab';

const lines = (s: string) => s.split(/\n|,/).map((x) => x.trim()).filter(Boolean);
type At = 'product' | 'subs';
type Msg = { at: At; text: string } | null;

export default function SetupTab() {
  const [product, setProduct] = useStore('product', null);
  const [subs, setSubs] = useStore('subreddits', []);
  const [settings] = useStore('settings', DEFAULT_SETTINGS);
  const [url, setUrl] = useState(product?.url ?? '');
  const [extra, setExtra] = useState('');
  // progress, errors and results are shown under the button that started them
  const [busy, setBusy] = useState<Msg>(null);
  const [err, setErr] = useState<Msg>(null);
  const [done, setDone] = useState<Msg>(null);
  const [manual, setManual] = useState('');

  const analyze = async () => {
    setErr(null);
    setDone(null);
    // check what we can before the browser's permission prompt, so it never fails after the user said yes
    if (!settings.deepseekKey) return setErr({ at: 'product', text: 'Add your DeepSeek API key first (Settings, the gear icon). Pigeon needs it to read your site.' });
    let u: string;
    try {
      u = /^https?:\/\//.test(url) ? url : `https://${url}`;
      new URL(u);
    } catch {
      return setErr({ at: 'product', text: 'That does not look like a website address.' });
    }
    try {
      setBusy({ at: 'product', text: 'Step 1 of 3: asking permission to read your site…' });
      const ok = await chrome.permissions.request({ origins: [new URL(u).origin + '/*'] });
      if (!ok) throw new Error('Permission to read your website was denied.');
      setBusy({ at: 'product', text: 'Step 2 of 3: reading your site…' });
      const text = await scrapeSite(u);
      setBusy({ at: 'product', text: 'Step 3 of 3: working out your ideal customer…' });
      setProduct(await buildProfile(u, text, extra));
      setDone({ at: 'product', text: 'Done. Check the profile below and edit anything that is off.' });
    } catch (e) {
      setErr({ at: 'product', text: (e as Error).message });
    } finally {
      setBusy(null);
    }
  };

  const find = async () => {
    if (!product) return;
    setErr(null);
    setDone(null);
    if (!settings.deepseekKey) return setErr({ at: 'subs', text: 'Add your DeepSeek API key first (Settings, the gear icon).' });
    try {
      setBusy({ at: 'subs', text: 'Looking for subreddits…' });
      const found = await discoverSubreddits(product, (t) => setBusy({ at: 'subs', text: t }));
      const old = new Map(subs.map((s) => [s.name.toLowerCase(), s]));
      const merged = found.map((s) => ({ ...s, enabled: old.get(s.name.toLowerCase())?.enabled ?? s.enabled, lastVisited: old.get(s.name.toLowerCase())?.lastVisited }));
      const keep = subs.filter((s) => !found.some((f) => f.name.toLowerCase() === s.name.toLowerCase()));
      setSubs([...merged, ...keep].sort((a, b) => b.score - a.score));
      setDone({ at: 'subs', text: `Checked ${found.length} subreddits. Ones whose rules ban promotion or DMs start switched off.` });
    } catch (e) {
      setErr({ at: 'subs', text: (e as Error).message });
    } finally {
      setBusy(null);
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
  const status = (at: At) => (
    <>
      {busy?.at === at && <Notice kind="info">{busy.text}</Notice>}
      {err?.at === at && <Notice kind="error" onClose={() => setErr(null)}>{err.text}</Notice>}
      {done?.at === at && <Notice kind="success" onClose={() => setDone(null)}>{done.text}</Notice>}
    </>
  );

  return (
    <>
      <div className="card">
        <h3>1. Your product</h3>
        <Field label="Website"><input type="text" placeholder="trypigeon.in" value={url} onChange={(e) => setUrl(e.target.value)} /></Field>
        <Field label="Anything the site doesn't say" hint="Who you sell to, what makes you different. Optional, but it sharpens your ideal customer profile.">
          <textarea value={extra} onChange={(e) => setExtra(e.target.value)} />
        </Field>
        <button className="btn pri" disabled={!url || !!busy} onClick={analyze}>{product ? 'Re-analyze' : 'Analyze my product'}</button>
        {status('product')}
      </div>

      {product && (
        <div className="card">
          <h3>2. Ideal customer profile <span className="mut small">(edit anything)</span></h3>
          <Field label="Name"><input type="text" value={product.name} onChange={(e) => edit({ name: e.target.value })} /></Field>
          <Field label="What it does"><textarea value={product.summary} onChange={(e) => edit({ summary: e.target.value })} /></Field>
          <Field label="What you offer them"><input type="text" value={product.offer} onChange={(e) => edit({ offer: e.target.value })} /></Field>
          <Field label="Ideal customer"><textarea value={product.icp} onChange={(e) => edit({ icp: e.target.value })} /></Field>
          <Field label="Pain phrases" hint="How they actually talk, one per line"><textarea value={product.painPhrases.join('\n')} onChange={(e) => edit({ painPhrases: lines(e.target.value) })} /></Field>
          <Field label="Buying signals" hint="Post patterns that mean 'now', one per line"><textarea value={product.buyingSignals.join('\n')} onChange={(e) => edit({ buyingSignals: lines(e.target.value) })} /></Field>
          <Field label="Competitors" hint="Comma separated"><input type="text" value={product.competitors.join(', ')} onChange={(e) => edit({ competitors: lines(e.target.value) })} /></Field>
          <Field label="Subreddit ideas" hint="Pigeon checks these against Reddit"><textarea value={product.subredditIdeas.join(', ')} onChange={(e) => edit({ subredditIdeas: lines(e.target.value) })} /></Field>
        </div>
      )}

      {product && (
        <div className="card">
          <h3>3. Where they hang out</h3>
          <div className="mut small mb-8">
            Each subreddit is checked live: member count, posting rate, how many of 25 recent titles fit your customer, and whether the rules ban promotion or DMs.
          </div>
          <button className="btn pri" disabled={!!busy} onClick={find}>{subs.length ? 'Re-run discovery' : 'Find subreddits'}</button>
          {status('subs')}
          <div className="row my-8">
            <input className="grow" type="text" aria-label="Add a subreddit" placeholder="add r/name manually" value={manual} onChange={(e) => setManual(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && addManual()} />
            <button className="btn" onClick={addManual}>Add</button>
          </div>
          {subs.map((s) => (
            <div className="item" key={s.name}>
              <div className="row">
                <input type="checkbox" aria-label={`Use r/${s.name}`} checked={s.enabled} onChange={(e) => setSubs(subs.map((x) => (x.name === s.name ? { ...x, enabled: e.target.checked } : x)))} />
                <a className="t" href={`https://www.reddit.com/r/${s.name}`} target="_blank" rel="noreferrer">r/{s.name}</a>
                <Pill tone={s.score >= 70 ? 'ok' : s.score >= 50 ? 'warn' : 'bad'}>Score {s.score}</Pill>
                <Pill tone={s.rulesRisk === 'low' ? 'ok' : s.rulesRisk === 'medium' ? 'warn' : 'bad'}>Rules risk: {s.rulesRisk}</Pill>
                <span className="grow" />
                <button className="btn sm" aria-label={`Remove r/${s.name}`} onClick={() => setSubs(subs.filter((x) => x.name !== s.name))}>Remove</button>
              </div>
              <div className="why small">
                {s.subscribers ? `${s.subscribers.toLocaleString()} members · ${s.postsPerDay}/day · ${s.icpDensity}% fit · ` : ''}{s.reason}
              </div>
            </div>
          ))}
        </div>
      )}

      <MessagesTab />
    </>
  );
}
