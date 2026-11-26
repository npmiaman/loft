import React from 'react';

export const send = <T = Record<string, unknown>,>(msg: Record<string, unknown>): Promise<{ ok: boolean; error?: string } & T> =>
  chrome.runtime.sendMessage(msg);

export function Field(p: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="f">
      {p.label}
      {p.hint && <span className="h">{p.hint}</span>}
      {p.children}
    </label>
  );
}

export function Toggle(p: { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string }) {
  return (
    <label className="row" style={{ marginBottom: 8, cursor: 'pointer' }}>
      <input type="checkbox" checked={p.checked} onChange={(e) => p.onChange(e.target.checked)} />
      <span>
        <b>{p.label}</b>
        {p.hint && <div className="mut small">{p.hint}</div>}
      </span>
    </label>
  );
}

export const pillFor = (n: number) => (n >= 80 ? 'hi' : n >= 60 ? 'mid' : 'lo');
export const today = () => new Date().toISOString().slice(0, 10);
