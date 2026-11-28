"use client";
import * as M from "@radix-ui/react-dropdown-menu";
import * as React from "react";
import { cn } from "@/lib/utils";

export const Menu = M.Root;
export const MenuTrigger = M.Trigger;
export function MenuContent({ className, ...p }: React.ComponentProps<typeof M.Content>) {
  return (
    <M.Portal>
      <M.Content align="end" sideOffset={6} className={cn("z-50 min-w-52 animate-pop rounded-xl border border-line bg-card p-1 shadow-pop", className)} {...p} />
    </M.Portal>
  );
}
export function MenuItem({ className, danger, ...p }: React.ComponentProps<typeof M.Item> & { danger?: boolean }) {
  return (
    <M.Item className={cn("flex cursor-pointer select-none items-center gap-2 rounded-md px-2.5 py-2 text-sm outline-none data-[disabled]:opacity-40 data-[highlighted]:bg-hover", danger && "text-bad", className)} {...p} />
  );
}
export const MenuSeparator = (p: React.ComponentProps<typeof M.Separator>) => <M.Separator className="my-1 h-px bg-line" {...p} />;
