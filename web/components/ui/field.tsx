import * as React from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

const base =
  "w-full rounded-lg border border-line2 bg-card px-3 text-ink placeholder:text-mute/80 transition-shadow focus:border-acc focus:outline-none focus:ring-[3px] focus:ring-acc/20";

export const Input = React.forwardRef<HTMLInputElement, React.ComponentProps<"input">>(({ className, ...p }, ref) => (
  <input ref={ref} className={cn(base, "h-9", className)} {...p} />
));
Input.displayName = "Input";

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.ComponentProps<"textarea">>(({ className, ...p }, ref) => (
  <textarea ref={ref} className={cn(base, "min-h-24 py-2.5 font-mono text-[12.5px]", className)} {...p} />
));
Textarea.displayName = "Textarea";

export function Select({ className, children, ...p }: React.ComponentProps<"select">) {
  return (
    <div className={cn("relative", className)}>
      <select className={cn(base, "h-9 appearance-none pr-8")} {...p}>{children}</select>
      <ChevronDown className="pointer-events-none absolute right-2.5 top-2.5 size-4 text-mute" aria-hidden />
    </div>
  );
}

export function Field({ label, hint, children, className }: { label: string; hint?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <label className={cn("block", className)}>
      <span className="mb-1.5 block text-[13px] font-medium text-ink2">{label}</span>
      {children}
      {hint && <span className="mt-1.5 block text-xs text-mute">{hint}</span>}
    </label>
  );
}
