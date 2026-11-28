"use client";
import { cva, type VariantProps } from "class-variance-authority";
import { motion } from "motion/react";
import { Loader2, type LucideIcon } from "lucide-react";
import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * Buttons, and anchors that look like buttons (`<a className={buttonStyles(...)}>`). Two densities that match the inputs:
 * md = 36px (pages, dialogs), sm = 32px (dense rows, the agent drawer). Icons are size-4 in md, size-3.5 in sm.
 * Focus uses the global :focus-visible outline (globals.css), nothing here.
 */
export const buttonStyles = cva(
  "inline-flex shrink-0 items-center justify-center whitespace-nowrap rounded-md font-medium transition-colors disabled:pointer-events-none disabled:opacity-45 select-none",
  {
    variants: {
      variant: {
        primary: "bg-ink text-on-ink hover:opacity-85",
        secondary: "border border-line2 bg-card text-ink hover:bg-hover",
        ghost: "text-ink2 hover:bg-hover",
        danger: "border border-line2 bg-card text-bad hover:border-bad hover:bg-bad-bg",
        dangerSolid: "bg-bad text-on-bad hover:opacity-90",
      },
      size: {
        sm: "h-8 gap-1.5 px-3 text-sm [&_svg]:size-3.5",
        md: "h-9 gap-2 px-3.5 [&_svg]:size-4",
        icon: "size-9 p-0 text-mute hover:text-ink [&_svg]:size-4",
        "icon-sm": "size-8 p-0 text-mute hover:text-ink [&_svg]:size-3.5",
      },
    },
    defaultVariants: { variant: "secondary", size: "md" },
  },
);

type Props = Omit<React.ComponentProps<typeof motion.button>, "children"> &
  VariantProps<typeof buttonStyles> & { loading?: boolean; children?: React.ReactNode };

/** A button that presses in slightly, and shows a spinner while its action runs (so people never double-click). */
export function Button({ className, variant, size, loading, children, disabled, ...p }: Props) {
  return (
    <motion.button
      whileTap={disabled || loading ? undefined : { scale: 0.97 }}
      transition={{ type: "spring", stiffness: 600, damping: 30 }}
      className={cn(buttonStyles({ variant, size }), className)}
      data-size={size ?? "md"}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...p}
    >
      {loading && <Loader2 className="animate-spin" aria-hidden />}
      {children}
    </motion.button>
  );
}

/** An icon-only button. The label is required: it is the accessible name and the hover tooltip. */
export function IconButton({ icon: I, label, size = "md", className, ...p }: Omit<Props, "size" | "children"> & { icon: LucideIcon; label: string; size?: "md" | "sm" }) {
  return (
    <Button variant="ghost" size={size === "sm" ? "icon-sm" : "icon"} aria-label={label} title={label} className={className} {...p}>
      <I aria-hidden />
    </Button>
  );
}
