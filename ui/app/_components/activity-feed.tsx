"use client";

import useSWR from "swr";
import { Send, MessageSquare, Handshake, AlertCircle } from "lucide-react";
import { relativeTime } from "@/lib/format";

type Item = {
  ts: string;
  kind: "sent" | "reply" | "linkedin" | "failed";
  text: string;
  detail: string;
  lead_id: string;
};

const ICON = {
  sent: <Send size={13} className="text-muted" />,
  reply: <MessageSquare size={13} className="text-green" />,
  linkedin: <Handshake size={13} className="text-muted" />,
  failed: <AlertCircle size={13} className="text-red-500" />,
};

/** Plain-English log of what the machine actually did. */
export function ActivityFeed() {
  const { data } = useSWR<{ items: Item[] }>("/api/v1/activity?limit=30", {
    refreshInterval: 30_000,
  });

  return (
    <div className="bg-paper border border-border rounded-md p-5">
      <div className="text-base font-semibold tracking-tight mb-3">Activity</div>
      {!data ? (
        <div className="text-muted text-sm py-3">Loading…</div>
      ) : data.items.length === 0 ? (
        <div className="text-muted text-sm py-3">
          Nothing yet. Sends, replies, and LinkedIn touches show up here.
        </div>
      ) : (
        <ul className="space-y-2.5 max-h-72 overflow-y-auto scrollbar-thin pr-1">
          {data.items.map((it, i) => (
            <li key={i} className="flex items-start gap-2.5 text-sm">
              <span className="mt-0.5 shrink-0">{ICON[it.kind]}</span>
              <div className="flex-1 min-w-0">
                <div className="text-ink/90 leading-snug">{it.text}</div>
                {it.detail ? (
                  <div className="text-xs text-muted truncate">{it.detail}</div>
                ) : null}
              </div>
              <span className="text-[10px] text-muted shrink-0 mt-0.5">
                {relativeTime(it.ts)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
