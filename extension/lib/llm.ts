// DeepSeek client (OpenAI-compatible). Docs: https://api-docs.deepseek.com/
import { getSettings } from './storage';

const ENDPOINT = 'https://api.deepseek.com/chat/completions';

export class LlmError extends Error {
  constructor(message: string, public status?: number) {
    super(message);
  }
}

interface ChatOpts {
  system: string;
  user: string;
  json?: boolean;
  maxTokens?: number;
  temperature?: number;
  /** Thinking is OFF unless asked for: it is slower and these tasks are mostly classification/writing. */
  thinking?: boolean;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function chat(o: ChatOpts): Promise<string> {
  const s = await getSettings();
  if (!s.deepseekKey) throw new LlmError('Add your DeepSeek API key in Settings first.');

  const body: Record<string, unknown> = {
    model: s.model,
    stream: false,
    max_tokens: o.maxTokens ?? 4000,
    thinking: { type: o.thinking ? 'enabled' : 'disabled' },
    messages: [
      { role: 'system', content: o.system },
      { role: 'user', content: o.user },
    ],
  };
  if (o.json) body.response_format = { type: 'json_object' };
  // temperature is ignored (no error) in thinking mode, so only send it otherwise
  if (!o.thinking) body.temperature = o.temperature ?? (o.json ? 0.2 : 0.8);

  let lastErr: unknown;
  for (let attempt = 0; attempt < 4; attempt++) {
    try {
      const res = await fetch(ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${s.deepseekKey}` },
        body: JSON.stringify(body),
      });
      if (res.status === 401) throw new LlmError('DeepSeek rejected the API key (401).', 401);
      if (res.status === 402) throw new LlmError('DeepSeek account has insufficient balance (402).', 402);
      if (res.status === 429 || res.status >= 500) {
        lastErr = new LlmError(`DeepSeek ${res.status}`, res.status);
        await sleep(1500 * 2 ** attempt);
        continue;
      }
      if (!res.ok) throw new LlmError(`DeepSeek ${res.status}: ${(await res.text()).slice(0, 300)}`, res.status);
      const data = await res.json();
      const content: string = data?.choices?.[0]?.message?.content ?? '';
      // JSON mode can return blank content when the model emits invalid JSON; retry.
      if (o.json && !content.trim()) {
        lastErr = new LlmError('DeepSeek returned empty JSON content');
        continue;
      }
      return content;
    } catch (e) {
      if (e instanceof LlmError && e.status && e.status < 500 && e.status !== 429) throw e;
      lastErr = e;
      await sleep(1000 * 2 ** attempt);
    }
  }
  throw lastErr instanceof Error ? lastErr : new LlmError('DeepSeek request failed');
}

export async function chatJson<T>(o: Omit<ChatOpts, 'json'>): Promise<T> {
  const text = await chat({ ...o, json: true });
  try {
    return JSON.parse(text) as T;
  } catch {
    const m = text.match(/\{[\s\S]*\}/);
    if (m) return JSON.parse(m[0]) as T;
    throw new LlmError('Could not parse JSON from DeepSeek: ' + text.slice(0, 200));
  }
}

/** "Test key": one tiny request, no retries, so the user gets a clear answer fast. */
export async function testKey(key: string, model: string): Promise<{ ok: boolean; status?: number; error?: string }> {
  if (!key) return { ok: false, error: 'No key entered' };
  try {
    const res = await fetch(ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${key}` },
      body: JSON.stringify({ model, stream: false, max_tokens: 1, thinking: { type: 'disabled' }, messages: [{ role: 'user', content: 'ping' }] }),
    });
    if (res.ok) return { ok: true };
    return { ok: false, status: res.status, error: `HTTP ${res.status}` };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}
