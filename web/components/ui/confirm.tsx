"use client";
import * as D from "@radix-ui/react-dialog";
import * as React from "react";
import { Button } from "./button";

export type ConfirmOpts = {
  /** Names the object: "Delete Reddit 1?", "Send this message to u/alice?" */
  title: string;
  /** What will happen, in one line. */
  body?: React.ReactNode;
  /** The verb on the button: "Delete account", "Send", "Post comment". */
  action: string;
  danger?: boolean;
  /** The safe way out. Default "Cancel"; e.g. "Keep editing". */
  cancel?: string;
};

const Ctx = React.createContext<(o: ConfirmOpts) => Promise<boolean>>(async () => false);
/** `const confirm = useConfirm(); if (await confirm({ title, body, action })) …` — the styled replacement for window.confirm. */
export const useConfirm = () => React.useContext(Ctx);

export function ConfirmProvider({ children }: { children: React.ReactNode }) {
  const [cur, setCur] = React.useState<(ConfirmOpts & { done: (ok: boolean) => void }) | null>(null);
  const confirm = React.useCallback((o: ConfirmOpts) => new Promise<boolean>((done) => setCur({ ...o, done })), []);
  const close = (ok: boolean) => { cur?.done(ok); setCur(null); };
  return (
    <Ctx.Provider value={confirm}>
      {children}
      <ConfirmDialog open={!!cur} title={cur?.title ?? ""} body={cur?.body} action={cur?.action ?? "OK"} danger={cur?.danger} cancel={cur?.cancel}
        onCancel={() => close(false)} onConfirm={() => close(true)} />
    </Ctx.Provider>
  );
}

/** A small modal that asks before something that can't be undone: the title names the object, one line says what happens. */
export function ConfirmDialog({ open, title, body, action, danger, cancel, onCancel, onConfirm }: ConfirmOpts & { open: boolean; onCancel: () => void; onConfirm: () => void }) {
  return (
    <D.Root open={open} onOpenChange={(o) => !o && onCancel()}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-[60] animate-fade bg-overlay" />
        <D.Content role="alertdialog"
          className="fixed left-1/2 top-1/2 z-[61] w-[min(420px,92vw)] -translate-x-1/2 -translate-y-1/2 animate-pop rounded-xl border border-line bg-card p-5 shadow-pop">
          <D.Title className="text-lg font-semibold tracking-tight">{title}</D.Title>
          {body ? <D.Description className="mt-1.5 text-sm text-mute">{body}</D.Description> : <D.Description className="sr-only">{title}</D.Description>}
          <div className="mt-5 flex justify-end gap-2">
            <Button onClick={onCancel} autoFocus>{cancel ?? "Cancel"}</Button>
            <Button variant={danger ? "dangerSolid" : "primary"} onClick={onConfirm}>{action}</Button>
          </div>
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}
