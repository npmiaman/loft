import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import type { LeadStatus } from '../../lib/types';

export const send = <T = Record<string, unknown>,>(msg: Record<string, unknown>): Promise<{ ok: boolean; error?: string } & T> =>
  chrome.runtime.sendMessage(msg);

export function Field(p: { label: string; hint?: string; error?: string; children: React.ReactNode; disabled?: boolean }) {
  return (
    <label className={`f${p.disabled ? ' off' : ''}`}>
      {p.label}
      {p.hint && <span className="h">{p.hint}</span>}
      {p.children}
      {p.error && <span className="ferr" role="alert">{p.error}</span>}
    </label>
  );
}

export function Toggle(p: { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string; disabled?: boolean }) {
  return (
    <label className={`toggle${p.disabled ? ' off' : ''}`}>
      <input type="checkbox" checked={p.checked} disabled={p.disabled} onChange={(e) => p.onChange(e.target.checked)} />
      <span>
        <b>{p.label}</b>
        {p.hint && <div className="mut small">{p.hint}</div>}
      </span>
    </label>
  );
}

/** Feedback placed right under the control that caused it. */
export function Notice(p: { kind: 'error' | 'warn' | 'info' | 'success'; children: React.ReactNode; onClose?: () => void; action?: React.ReactNode }) {
  if (p.children == null || p.children === '') return null;
  return (
    <div className={`notice ${p.kind}`} role={p.kind === 'error' ? 'alert' : 'status'}>
      <span className="grow">{p.children}</span>
      {p.action}
      {p.onClose && <button className="btn ghost sm" aria-label="Dismiss" onClick={p.onClose}>Dismiss</button>}
    </div>
  );
}

export function EmptyState(p: { title: string; children?: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="empty">
      <b>{p.title}</b>
      {p.children && <div className="mut small">{p.children}</div>}
      {p.action && <div className="mt-8">{p.action}</div>}
    </div>
  );
}

// ---- one status map for every pill
export const LEAD_STATUS: Record<LeadStatus, { label: string; tone: 'neutral' | 'warn' | 'info' | 'ok' | 'bad' | 'muted' }> = {
  queued: { label: 'Needs a message', tone: 'neutral' },
  draft: { label: 'Waiting for approval', tone: 'warn' },
  ready: { label: 'Approved', tone: 'info' },
  sent: { label: 'Sent', tone: 'ok' },
  replied: { label: 'Replied', tone: 'ok' },
  failed: { label: 'Failed', tone: 'bad' },
  skipped: { label: 'Skipped', tone: 'muted' },
};
export const ITEM_STATUS: Record<'pending' | 'posted' | 'skipped' | 'failed', { label: string; tone: 'warn' | 'ok' | 'muted' | 'bad' }> = {
  pending: { label: 'Waiting for approval', tone: 'warn' },
  posted: { label: 'Posted', tone: 'ok' },
  skipped: { label: 'Skipped', tone: 'muted' },
  failed: { label: 'Failed', tone: 'bad' },
};
export const Pill = (p: { tone: string; children: React.ReactNode }) => <span className={`pill t-${p.tone}`}>{p.children}</span>;
export const StatusPill = ({ status }: { status: LeadStatus }) => <Pill tone={LEAD_STATUS[status].tone}>{LEAD_STATUS[status].label}</Pill>;
export const fitTone = (n: number) => (n >= 80 ? 'ok' : n >= 60 ? 'warn' : 'bad');
export const FitPill = ({ fit }: { fit: number }) => <Pill tone={fitTone(fit)}>Fit {fit}</Pill>;

export const today = () => new Date().toISOString().slice(0, 10);

// ---- in-panel confirm dialog (replaces window.confirm)
export interface ConfirmOpts {
  title: string;
  body?: React.ReactNode;
  confirm: string;
  danger?: boolean;
}
type Ask = (o: ConfirmOpts) => Promise<boolean>;
const ConfirmCtx = createContext<Ask>(async () => false);
export const useConfirm = () => useContext(ConfirmCtx);

export function ConfirmProvider({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState<(ConfirmOpts & { resolve: (v: boolean) => void }) | null>(null);
  const ok = useRef<HTMLButtonElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const back = useRef<Element | null>(null);
  const ask = useCallback<Ask>((o) => new Promise((resolve) => {
    back.current = document.activeElement;
    setOpen({ ...o, resolve });
  }), []);
  const close = (v: boolean) => {
    open?.resolve(v);
    setOpen(null);
    (back.current as HTMLElement | null)?.focus?.();
  };
  useEffect(() => {
    if (!open) return;
    (open.danger ? cancel : ok).current?.focus();
    const k = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close(false);
      if (e.key === 'Tab') { // keep focus inside the dialog
        const a = cancel.current, b = ok.current;
        if (e.shiftKey && document.activeElement === a) { e.preventDefault(); b?.focus(); }
        else if (!e.shiftKey && document.activeElement === b) { e.preventDefault(); a?.focus(); }
      }
    };
    document.addEventListener('keydown', k);
    return () => document.removeEventListener('keydown', k);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  return (
    <ConfirmCtx.Provider value={ask}>
      {children}
      {open && (
        <div className="scrim" onClick={(e) => e.target === e.currentTarget && close(false)}>
          <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="dlg-t">
            <h3 id="dlg-t">{open.title}</h3>
            {open.body && <div className="dlg-body">{open.body}</div>}
            <div className="row end mt-12">
              <button ref={cancel} className="btn" onClick={() => close(false)}>Cancel</button>
              <button ref={ok} className={`btn ${open.danger ? 'danger solid' : 'pri'}`} onClick={() => close(true)}>{open.confirm}</button>
            </div>
          </div>
        </div>
      )}
    </ConfirmCtx.Provider>
  );
}
