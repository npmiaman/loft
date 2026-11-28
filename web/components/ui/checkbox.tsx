"use client";
import { Check } from "lucide-react";
import * as React from "react";
import { cn } from "@/lib/utils";

type Props = { checked: boolean; onChange: (v: boolean) => void; label?: React.ReactNode; "aria-label"?: string; disabled?: boolean; id?: string; className?: string };

/** A checkbox that matches the rest of the UI (keyboard: Space toggles). Pass `label` for visible text, or aria-label. */
export function Checkbox({ checked, onChange, label, disabled, className, ...p }: Props) {
  const lid = React.useId();
  const box = (
    <button type="button" role="checkbox" aria-checked={checked} disabled={disabled} id={p.id} aria-label={label ? undefined : p["aria-label"]} aria-labelledby={label ? lid : undefined}
      onClick={() => onChange(!checked)}
      className={cn("grid size-4 shrink-0 place-items-center rounded-sm border transition-colors disabled:opacity-45",
        checked ? "border-ink bg-ink text-on-ink" : "border-line2 bg-card", !label && className)}>
      {checked && <Check className="size-3" strokeWidth={3} aria-hidden />}
    </button>
  );
  if (!label) return box;
  return <label className={cn("inline-flex cursor-pointer select-none items-center gap-2 text-sm", disabled && "cursor-not-allowed opacity-60", className)}>{box}<span id={lid}>{label}</span></label>;
}

/** An on/off switch, for settings that take effect straight away. */
export function Switch({ checked, onChange, label, disabled, className, ...p }: Props) {
  const lid = React.useId();
  const sw = (
    <button type="button" role="switch" aria-checked={checked} disabled={disabled} id={p.id} aria-label={label ? undefined : p["aria-label"]} aria-labelledby={label ? lid : undefined}
      onClick={() => onChange(!checked)}
      className={cn("relative h-5 w-9 shrink-0 rounded-full transition-colors disabled:opacity-45", checked ? "bg-ok-solid" : "bg-line2", !label && className)}>
      <span className={cn("absolute top-0.5 left-0.5 size-4 rounded-full bg-white shadow-card transition-transform", checked && "translate-x-4")} />
    </button>
  );
  if (!label) return sw;
  return <label className={cn("inline-flex cursor-pointer select-none items-center gap-2 text-sm", className)}>{sw}<span id={lid}>{label}</span></label>;
}
