import { useState } from 'react';
import { addSpintax, countVariants, previewVariants } from '../../../lib/brain';
import { DEFAULT_SETTINGS, DEFAULT_TEMPLATE } from '../../../lib/storage';
import { useLeads, useStore } from '../../../lib/useStore';
import { Field, Toggle } from '../ui';

export default function MessagesTab() {
  const [settings, setSettings] = useStore('settings', DEFAULT_SETTINGS);
  const [templates, setTemplates] = useStore('templates', [DEFAULT_TEMPLATE]);
  const leads = useLeads();
  const [intensity, setIntensity] = useState<'light' | 'medium' | 'heavy'>('medium');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [previews, setPreviews] = useState<string[]>([]);
  const [undo, setUndo] = useState<string | null>(null);

  const tpl = templates[0] ?? DEFAULT_TEMPLATE;
  const setBody = (body: string) => setTemplates([{ ...tpl, body }, ...templates.slice(1)]);
  const sample = leads[0] ?? { username: 'alex_dev', subreddit: 'SaaS', postTitle: 'How do you find your first 10 customers?' };
  const variants = countVariants(tpl.body);

  const spin = async () => {
    setErr('');
    setBusy(true);
    try {
      const out = await addSpintax(tpl.body, intensity);
      setUndo(tpl.body);
      setBody(out);
      setPreviews(previewVariants(out, sample, 4));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="card">
        <h3>How messages get written</h3>
        <div className="seg" style={{ marginBottom: 10 }}>
          <button className={settings.dmMode === 'ai' ? 'on' : ''} onClick={() => setSettings({ ...settings, dmMode: 'ai' })}>AI writes each DM</button>
          <button className={settings.dmMode === 'template' ? 'on' : ''} onClick={() => setSettings({ ...settings, dmMode: 'template' })}>Use my template</button>
        </div>
        {settings.dmMode === 'ai' ? (
          <>
            <div className="mut small" style={{ marginBottom: 8 }}>
              Each message is written for the person, referencing what they posted and why they fit. Leads without a post (imported lists) fall back to your template.
            </div>
            <Toggle checked={settings.approveAiDrafts} onChange={(v) => setSettings({ ...settings, approveAiDrafts: v })} label="Approve AI drafts before sending" hint="Recommended until you trust the tone." />
            <Field label="Extra guidance for the writer" hint="Tone, what to emphasize, what to avoid.">
              <textarea value={settings.aiGuidance} onChange={(e) => setSettings({ ...settings, aiGuidance: e.target.value })} placeholder="Friendly, a bit informal. Mention I'm a solo founder. Never promise results." />
            </Field>
          </>
        ) : (
          <div className="mut small">Your template below is sent as-is after spintax and variables are filled in.</div>
        )}
      </div>

      <div className="card">
        <h3>Template</h3>
        <Field label="Message" hint="Variables: {name} {subreddit} {topic}. Spintax: {Hey|Hi|Hello}, one option is picked at random.">
          <textarea style={{ minHeight: 130 }} value={tpl.body} onChange={(e) => setBody(e.target.value)} />
        </Field>
        <div className="small mut" style={{ marginBottom: 10 }}>
          {variants > 1 ? <>This template can render <b>{variants.toLocaleString()}</b> different messages.</> : 'No spintax yet: every message would read the same. Add some below.'}
          {' '}Keep the first message free of links.
        </div>

        <div className="row">
          <select value={intensity} onChange={(e) => setIntensity(e.target.value as typeof intensity)} style={{ width: 'auto' }}>
            <option value="light">light spin</option>
            <option value="medium">medium spin</option>
            <option value="heavy">heavy spin</option>
          </select>
          <button className="btn pri grow" disabled={busy || !tpl.body.trim()} onClick={spin}>{busy ? 'Spinning…' : '✨ Add spintax with AI'}</button>
        </div>
        <div className="row" style={{ marginTop: 8 }}>
          <button className="btn sm" onClick={() => setPreviews(previewVariants(tpl.body, sample, 4))}>Preview variations</button>
          {undo != null && <button className="btn sm" onClick={() => (setBody(undo), setUndo(null), setPreviews([]))}>Undo AI spin</button>}
        </div>
        {err && <div className="err small" style={{ marginTop: 8 }}>{err}</div>}
        {previews.map((p, i) => <div className="preview" key={i}>{p}</div>)}
        {previews.length > 0 && <div className="mut small" style={{ marginTop: 6 }}>Previewed with u/{sample.username} in r/{sample.subreddit}.</div>}
      </div>
    </>
  );
}
