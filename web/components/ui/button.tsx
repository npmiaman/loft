"use client";
import { cva, type VariantProps } from "class-variance-authority";
import { motion } from "motion/react";
import { Loader2 } from "lucide-react";
import * as React from "react";
import { cn } from "@/lib/utils";

export const buttonStyles = cva(
  "inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-lg font-medium transition-colors disabled:pointer-events-none disabled:opacity-45 select-none",
  {
    variants: {
      variant: {
        primary: "bg-ink text-on-ink hover:opacity-85",
        secondary: "border border-line2 bg-card text-ink hover:bg-hover",
        ghost: "text-ink2 hover:bg-hover",
        danger: "border border-line2 bg-card text-bad hover:border-bad hover:bg-bad-bg",
      },
      size: { sm: "h-8 px-3 text-[13px]", md: "h-9 px-3.5", icon: "size-8 p-0 text-mute hover:text-ink" },
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
      disabled={disabled || loading}
      {...p}
    >
      {loading && <Loader2 className="size-4 animate-spin" aria-hidden />}
      {children}
    </motion.button>
  );
}
