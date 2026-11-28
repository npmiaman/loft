import * as React from "react";
import { cn } from "@/lib/utils";

export type Col = { label: string; num?: boolean; className?: string };

/** The one table: quiet header, row dividers, numeric columns right-aligned with tabular figures, an empty state in the body. */
export function DataTable({ cols, rows, empty, dense, className }: {
  cols: (string | Col)[]; rows: React.ReactNode[][]; empty: React.ReactNode; dense?: boolean; className?: string;
}) {
  const cs = cols.map((c) => (typeof c === "string" ? { label: c } : c));
  const pad = dense ? "px-3 py-2" : "px-4 py-3";
  return (
    <div className={cn("overflow-x-auto", className)}>
      <table className={cn("w-full", dense ? "text-sm" : "text-sm")}>
        <thead><tr>{cs.map((c) => <th key={c.label} scope="col" className={cn(pad, "text-xs font-medium text-mute", c.num ? "text-right" : "text-left", c.className)}>{c.label}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-line hover:bg-hover/45">
              {r.map((v, j) => <td key={j} className={cn(pad, cs[j]?.num && "text-right tabular-nums", cs[j]?.className)}>{v}</td>)}
            </tr>
          ))}
          {!rows.length && <tr><td colSpan={cs.length} className="p-0">{empty}</td></tr>}
        </tbody>
      </table>
    </div>
  );
}
