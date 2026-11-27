import * as React from "react";
import { cn, hue } from "@/lib/utils";

export function Dot({ tone = "idle", className }: { tone?: "idle" | "ok" | "warn" | "bad"; className?: string }) {
  return (
    <i
      className={cn(
        "inline-block size-2 shrink-0 rounded-full",
        tone === "idle" && "bg-line2", tone === "ok" && "bg-ok shadow-[0_0_0_3px_color-mix(in_srgb,var(--ok)_22%,transparent)]",
        tone === "warn" && "bg-warn", tone === "bad" && "bg-bad", className,
      )}
    />
  );
}

export function Pill({ children, tone, className }: { children: React.ReactNode; tone?: "ok" | "warn" | "bad"; className?: string }) {
  return (
    <span className={cn("inline-flex h-[22px] items-center gap-1 rounded-full bg-hover px-2.5 text-xs font-medium text-ink2",
      tone === "ok" && "bg-ok-bg text-ok", tone === "warn" && "bg-warn-bg text-warn", tone === "bad" && "bg-bad-bg text-bad", className)}>
      {children}
    </span>
  );
}

export function Avatar({ name, size = 36 }: { name: string; size?: number }) {
  const h = hue(name);
  return (
    <span
      className="grid shrink-0 place-items-center rounded-full font-semibold"
      style={{ width: size, height: size, fontSize: size * 0.4, background: `light-dark(hsl(${h} 60% 92%), hsl(${h} 30% 22%))`, color: `light-dark(hsl(${h} 50% 30%), hsl(${h} 65% 80%))` }}
    >
      {(name.trim()[0] || "?").toUpperCase()}
    </span>
  );
}

export function Callout({ children, tone, action, icon }: { children: React.ReactNode; tone?: "warn" | "bad"; action?: React.ReactNode; icon?: React.ReactNode }) {
  return (
    <div className={cn("flex items-center gap-3 rounded-xl border border-line bg-hover/60 px-4 py-3", tone === "warn" && "border-transparent bg-warn-bg", tone === "bad" && "border-transparent bg-bad-bg text-bad")}>
      {icon}
      <div className="min-w-0 flex-1 text-[13.5px]">{children}</div>
      {action}
    </div>
  );
}

export const Card = ({ className, ...p }: React.ComponentProps<"div">) => <div className={cn("rounded-xl border border-line bg-card shadow-card", className)} {...p} />;

export function PageHead({ title, sub, actions }: { title: string; sub?: string; actions?: React.ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-[22px] font-semibold leading-7 tracking-[-0.02em]">{title}</h1>
        {sub && <p className="mt-1 text-mute">{sub}</p>}
      </div>
      <div className="flex flex-wrap items-center gap-2">{actions}</div>
    </div>
  );
}
