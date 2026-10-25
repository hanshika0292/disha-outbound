"use client";

import { cn } from "@/lib/cn";

type Tier = "A" | "B" | "C" | "D" | string | null | undefined;

export function TierBadge({ tier }: { tier: Tier }) {
  if (!tier) return <span className="text-muted text-xs">—</span>;
  const colorByTier: Record<string, string> = {
    A: "text-tier-a border-tier-a/40",
    B: "text-tier-b border-tier-b/40",
    C: "text-tier-c border-tier-c/40",
    D: "text-tier-d border-border-strong",
  };
  const cls = colorByTier[String(tier).toUpperCase()] ?? colorByTier.D;
  return (
    <span
      className={cn(
        "inline-flex items-center justify-center w-5 h-5 rounded-sm text-[11px] font-semibold border bg-paper",
        cls,
      )}
    >
      {String(tier).toUpperCase()}
    </span>
  );
}

const STATE_LABEL: Record<string, string> = {
  new: "New",
  scored: "Scored",
  approved: "Approved",
  in_sequence: "In sequence",
  paused: "Paused",
  replied: "Replied",
  meeting_booked: "Meeting",
  closed_won: "Won",
  closed_lost: "Lost",
  rejected: "Rejected",
};

// Outlined badge + colored dot: state is signalled by the dot, not a loud
// background wash. Reads as data, not decoration.
const STATE_DOT: Record<string, string> = {
  new: "bg-border-strong",
  scored: "bg-terracotta",
  approved: "bg-green",
  in_sequence: "bg-green",
  paused: "bg-warning",
  replied: "bg-terracotta",
  meeting_booked: "bg-green",
  closed_won: "bg-green",
  closed_lost: "bg-border-strong",
  rejected: "bg-border-strong",
};

export function StateBadge({ state }: { state: string | null | undefined }) {
  if (!state) return <span className="text-muted text-xs">—</span>;
  const dot = STATE_DOT[state] ?? "bg-border-strong";
  return (
    <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-sm text-[11px] font-medium text-ink/70 border border-border bg-paper">
      <span className={cn("w-1.5 h-1.5 rounded-full shrink-0", dot)} />
      {STATE_LABEL[state] ?? state.replace(/_/g, " ")}
    </span>
  );
}

export function ChannelBadge({ channel }: { channel: string | null | undefined }) {
  if (!channel) return <span className="text-muted text-xs">—</span>;
  const isEmail = channel === "email";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 text-[11px] font-medium uppercase tracking-wider px-1.5 py-0.5 border rounded-sm",
        isEmail
          ? "border-border-strong text-ink"
          : "border-terracotta/40 text-terracotta",
      )}
    >
      {channel}
    </span>
  );
}

export function StatusDot({
  ok,
  label,
}: {
  ok: boolean | null | undefined;
  label?: string;
}) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted">
      <span
        className={cn(
          "inline-block w-1.5 h-1.5 rounded-full",
          ok === true && "bg-green",
          ok === false && "bg-terracotta",
          ok === null || ok === undefined ? "bg-border-strong" : "",
        )}
      />
      {label}
    </span>
  );
}
