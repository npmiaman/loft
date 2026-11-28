"use client";
import * as D from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import * as React from "react";
import { cn } from "@/lib/utils";

/** Clicking a toast (e.g. its "Add AI key" button) must not count as clicking outside an open dialog and close it. */
const keepToasts = (e: { target: EventTarget | null; preventDefault: () => void }) => {
  if ((e.target as Element | null)?.closest?.("[data-sonner-toaster]")) e.preventDefault();
};

/** A centered modal. Escape and the backdrop close it; focus is trapped and restored (Radix). */
export function Modal({ open, onOpenChange, title, description, children, className }: {
  open: boolean; onOpenChange: (o: boolean) => void; title: string; description?: string; children: React.ReactNode; className?: string;
}) {
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-40 animate-fade bg-overlay" />
        <D.Content
          className={cn("fixed left-1/2 top-1/2 z-50 max-h-[90vh] w-[min(520px,94vw)] -translate-x-1/2 -translate-y-1/2 animate-pop overflow-y-auto rounded-xl border border-line bg-card p-6 shadow-pop", className)}
          onInteractOutside={keepToasts}
        >
          <div className="mb-4 flex items-start justify-between gap-4">
            <div>
              <D.Title className="text-lg font-semibold tracking-tight">{title}</D.Title>
              {description ? <D.Description className="mt-1 text-sm text-mute">{description}</D.Description> : <D.Description className="sr-only">{title}</D.Description>}
            </div>
            <D.Close className="grid size-8 place-items-center rounded-md text-mute hover:bg-hover hover:text-ink" aria-label="Close" title="Close"><X className="size-4" aria-hidden /></D.Close>
          </div>
          {children}
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}

/** A panel that slides in from the right, so the page behind it keeps its place. */
export function Drawer({ open, onOpenChange, children, label }: { open: boolean; onOpenChange: (o: boolean) => void; children: React.ReactNode; label: string }) {
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-40 animate-fade bg-overlay" />
        <D.Content onInteractOutside={keepToasts} className="fixed inset-y-0 right-0 z-50 w-[min(880px,100vw)] animate-slide overflow-y-auto border-l border-line bg-card shadow-pop focus:outline-none">
          <D.Title className="sr-only">{label}</D.Title>
          <D.Description className="sr-only">{label}</D.Description>
          {children}
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}
export const DrawerClose = D.Close;
