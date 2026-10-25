"use client";

import { ReactNode } from "react";
import { cn } from "@/lib/cn";

export function StatCard({
  label,
  value,
  hint,
  accent,
  icon,
  href,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  accent?: "terracotta" | "green" | "warning" | null;
  icon?: ReactNode;
  href?: string;
}) {
  const accentDot = {
    terracotta: "bg-terracotta",
    green: "bg-green",
    warning: "bg-warning",
  } as const;

  const inner = (
    <div
      className={cn(
        "relative bg-paper border border-border rounded-md p-4 flex flex-col gap-1 h-full",
        href && "cursor-pointer hover:border-border-strong",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="inline-flex items-center gap-1.5 text-[11px] uppercase tracking-[0.06em] text-muted font-medium">
          {accent ? (
            <span className={cn("w-1.5 h-1.5 rounded-full", accentDot[accent])} />
          ) : null}
          {label}
        </span>
        {icon ? <span className="shrink-0 text-muted/60">{icon}</span> : null}
      </div>
      <div className="text-2xl font-semibold tracking-tight text-ink leading-none mt-1.5 tabular-nums">
        {value}
      </div>
      {hint ? <div className="text-xs text-muted mt-1">{hint}</div> : null}
    </div>
  );

  if (href) {
    return (
      <a href={href} className="block h-full group">
        {inner}
      </a>
    );
  }
  return inner;
}
