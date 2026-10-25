"use client";

import useSWR from "swr";
import Link from "next/link";
import { ArrowRight, CheckCircle2 } from "lucide-react";

type Item = {
  label: string;
  sub: string;
  href: string;
  priority: number;
  count: number;
};

type Worklist = { items: Item[]; paused: boolean };

/** "What needs you today" — action-oriented list at the top of the dashboard. */
export function Worklist() {
  const { data } = useSWR<Worklist>("/api/v1/activity/worklist", {
    refreshInterval: 30_000,
  });

  if (!data) return null;

  if (data.items.length === 0) {
    return (
      <div className="bg-paper border border-border rounded-md p-4 flex items-center gap-2.5">
        <CheckCircle2 size={16} className="text-green" />
        <span className="text-sm text-ink">
          You&apos;re all caught up — the machine is running in the background.
        </span>
      </div>
    );
  }

  return (
    <div className="bg-paper border border-border rounded-md overflow-hidden">
      <div className="px-4 py-2.5 border-b border-border bg-cream">
        <span className="text-[11px] uppercase tracking-[0.06em] text-muted font-semibold">
          Needs you today
        </span>
      </div>
      <ul className="divide-y divide-border">
        {data.items.map((it) => (
          <li key={it.href + it.label}>
            <Link
              href={it.href}
              className="flex items-center gap-3 px-4 py-3 hover:bg-cream/60 transition-colors group"
            >
              {it.count > 0 ? (
                <span className="shrink-0 min-w-[26px] h-6 px-1.5 inline-flex items-center justify-center rounded-md bg-terracotta text-white text-xs font-bold tabular-nums">
                  {it.count}
                </span>
              ) : (
                <span className="shrink-0 w-2 h-2 rounded-full bg-warning" />
              )}
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium text-ink">{it.label}</div>
                <div className="text-xs text-muted">{it.sub}</div>
              </div>
              <ArrowRight
                size={15}
                className="text-muted shrink-0 group-hover:text-ink transition-colors"
              />
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
