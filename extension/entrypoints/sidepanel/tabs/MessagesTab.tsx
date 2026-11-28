import { useState } from 'react';
import { addSpintax, countVariants, previewVariants } from '../../../lib/brain';
import { DEFAULT_SETTINGS, DEFAULT_TEMPLATE } from '../../../lib/storage';
import { useLeads, useStore } from '../../../lib/useStore';
import { Field, Notice } from '../ui';

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
        <h3>4. How DMs get written</h3>
        <div className="seg mb-8">
          <button className={settings.dmMode === 'ai' ? 'on' : ''} onClick={() => setSettings({ ...settings, dmMode: 'ai' })}>AI writes each DM</button>
          <button className={settings.dmMode === 'template' ? 'on' : ''} onClick={() => setSettings({ ...settings, dmMode: 'template' })}>Use my template</button>
        </div>
        {settings.dmMode === 'ai' ? (
          <>
            <div className="mut small mb-8">
              Each message is written for the person, referencing what they posted and why they fit. Leads without a post (imported lists) fall back to your template.
            </div>
            <Field label="Extra guidance for the writer" hint="Tone, what to emphasize, what to avoid.">
              <textarea value={settings.aiGuidance} onChange={(e) => setSettings({ ...settings, aiGuidance: e.target.value })} placeholder="Friendly, a bit informal. Mention I'm a solo founder. Never promise results." />
            </Field>
          </>
        ) : (
          <div className="mut small">Your template below is filled in (spintax and variables) for each person.</div>
        )}
      </div>

      <div className="card">
        <div className="notice info mb-8">Every message waits for your approval before it is sent, whether the AI wrote it or it came from your template.</div>
        <h3>Template</h3>
        <Field label="Message" hint="Variables: {name} {subreddit} {topic}. Spintax: {Hey|Hi|Hello}, one option is picked at random.">
          <textarea className="tall" value={tpl.body} onChange={(e) => setBody(e.target.value)} />
        </Field>
        <div className="small mut mb-8">
          {variants > 1 ? <>This template can render <b>{variants.toLocaleString()}</b> different messages.</> : 'No spintax yet: every message would read the same. Add some below.'}
          {' '}Keep the first message free of links.
        </div>

        <div className="row">
          <select aria-label="How much variation" value={intensity} onChange={(e) => setIntensity(e.target.value as typeof intensity)} className="w-auto">
            <option value="light">light spin</option>
            <option value="medium">medium spin</option>
            <option value="heavy">heavy spin</option>
          </select>
          <button className="btn pri grow" disabled={busy || !tpl.body.trim()} onClick={spin}>{busy ? 'Adding variations…' : 'Add spintax with AI'}</button>
        </div>
        <div className="row mt-8">
          <button className="btn sm" onClick={() => setPreviews(previewVariants(tpl.body, sample, 4))}>Preview variations</button>
          {undo != null && <button className="btn sm" onClick={() => (setBody(undo), setUndo(null), setPreviews([]))}>Undo AI spin</button>}
        </div>
        {err && <Notice kind="error" onClose={() => setErr('')}>{err}</Notice>}
        {previews.map((p, i) => <div className="preview" key={i}>{p}</div>)}
        {previews.length > 0 && <div className="mut small mt-8">Previewed with u/{sample.username} in r/{sample.subreddit}.</div>}
      </div>
    </>
  );
}
