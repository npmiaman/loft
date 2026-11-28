import * as React from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

/** Inputs match the buttons: md = 36px, sm = 32px. Focus is the global :focus-visible outline. */
type Density = "sm" | "md";
const base = "w-full rounded-md border border-line2 bg-card px-3 text-ink transition-colors disabled:opacity-60 aria-[invalid=true]:border-bad";
const h = (d: Density) => (d === "sm" ? "h-8 text-sm" : "h-9");

export const Input = React.forwardRef<HTMLInputElement, React.ComponentProps<"input"> & { density?: Density }>(({ className, density = "md", ...p }, ref) => (
  <input ref={ref} data-size={density} className={cn(base, h(density), className)} {...p} />
));
Input.displayName = "Input";

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.ComponentProps<"textarea"> & { mono?: boolean }>(({ className, mono, ...p }, ref) => (
  <textarea ref={ref} className={cn(base, "min-h-24 py-2", mono && "font-mono text-xs", className)} {...p} />
));
Textarea.displayName = "Textarea";

export function Select({ className, children, density = "md", ...p }: React.ComponentProps<"select"> & { density?: Density }) {
  return (
    <div className={cn("relative", className)}>
      <select data-size={density} className={cn(base, h(density), "appearance-none pr-8")} {...p}>{children}</select>
      <ChevronDown className={cn("pointer-events-none absolute right-2.5 size-4 text-mute", density === "sm" ? "top-2" : "top-2.5")} aria-hidden />
    </div>
  );
}

export function Field({ label, hint, children, className }: { label: string; hint?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <label className={cn("block", className)}>
      <span className="mb-1.5 block text-sm font-medium text-ink2">{label}</span>
      {children}
      {hint && <span className="mt-1.5 block text-xs text-mute">{hint}</span>}
    </label>
  );
}
