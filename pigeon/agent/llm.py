"""DeepSeek client (OpenAI-compatible). Thinking is off: these jobs are classification and short writing."""
from __future__ import annotations

import json
import re
import time

import httpx

ENDPOINT = "https://api.deepseek.com/chat/completions"


class LlmError(Exception):
    def __init__(self, msg: str, status: int | None = None):
        super().__init__(msg)
        self.status = status


class Llm:
    def __init__(self, key: str, model: str = "deepseek-flash", transport: httpx.BaseTransport | None = None,
                 endpoint: str = ENDPOINT):
        self.key, self.model, self.endpoint = key, model, endpoint
        self.http = httpx.Client(timeout=120, transport=transport)

    def chat(self, system: str, user: str, *, json_mode: bool = False, max_tokens: int = 4000,
             temperature: float | None = None) -> str:
        if not self.key:
            raise LlmError("No DeepSeek key: `pigeon agent config --deepseek-key KEY` or set DEEPSEEK_API_KEY.")
        body = dict(model=self.model, stream=False, max_tokens=max_tokens, thinking={"type": "disabled"},
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    temperature=temperature if temperature is not None else (0.2 if json_mode else 0.8))
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        last: Exception | None = None
        for attempt in range(4):
            try:
                r = self.http.post(self.endpoint, json=body, headers={"Authorization": f"Bearer {self.key}"})
                if r.status_code == 401:
                    raise LlmError("DeepSeek rejected the API key (401).", 401)
                if r.status_code == 402:
                    raise LlmError("DeepSeek account has insufficient balance (402).", 402)
                if r.status_code == 429 or r.status_code >= 500:
                    last = LlmError(f"DeepSeek {r.status_code}", r.status_code)
                    time.sleep(1.5 * 2 ** attempt)
                    continue
                if not r.is_success:
                    raise LlmError(f"DeepSeek {r.status_code}: {r.text[:300]}", r.status_code)
                content = r.json()["choices"][0]["message"]["content"] or ""
                if json_mode and not content.strip():  # JSON mode can come back blank; retry
                    last = LlmError("DeepSeek returned empty JSON content")
                    continue
                return content
            except LlmError as e:
                if e.status and e.status < 500 and e.status != 429:
                    raise
                last = e
            except httpx.HTTPError as e:
                last = e
                time.sleep(2 ** attempt)
        raise last if isinstance(last, Exception) else LlmError("DeepSeek request failed")

    def json(self, system: str, user: str, **kw) -> dict:
        text = self.chat(system, user, json_mode=True, **kw)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"\{[\s\S]*\}", text)
            if m:
                return json.loads(m.group(0))
            raise LlmError("Could not parse JSON from DeepSeek: " + text[:200])
