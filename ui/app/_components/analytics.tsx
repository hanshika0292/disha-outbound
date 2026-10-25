"use client";

import useSWR from "swr";
import {
  ResponsiveContainer,
  ComposedChart,
  Bar,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from "recharts";
import { Reply, CalendarCheck, Send, ShieldCheck, Sparkles } from "lucide-react";
import { formatNumber } from "@/lib/format";
import { SkeletonRows } from "@/components/ui/skeleton";

type Analytics = {
  funnel: Array<{ key: string; label: string; count: number }>;
  emails_sent: number;
  replies_total: number;
  interested_replies: number;
  meetings: number;
  contacted_leads: number;
  replied_leads: number;
  reply_rate: number;
  interested_rate: number;
  email_health: Record<string, number>;
  sendable_leads: number;
  trend: Array<{ date: string; sent: number; replies: number }>;
};

const pct = (n: number) => `${(n * 100).toFixed(n >= 0.1 ? 0 : 1)}%`;

const HEALTH_ORDER = ["verified", "risky", "guessed", "unknown", "bounced"] as const;
const HEALTH_LABEL: Record<string, string> = {
  verified: "Verified",
  risky: "Risky",
  guessed: "Guessed",
  unknown: "Unknown",
  bounced: "Bounced",
};
const HEALTH_COLOR: Record<string, string> = {
  verified: "var(--green)",
  risky: "var(--warning)",
  guessed: "var(--terracotta-soft)",
  unknown: "var(--border-strong)",
  bounced: "#e24b4a",
};

function Kpi({
  icon,
  label,
  value,
  hint,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="bg-paper border border-border rounded-sm p-4">
      <div className="flex items-center gap-1.5 text-muted mb-2">
        {icon}
        <span className="text-[11px] uppercase tracking-wider">{label}</span>
      </div>
      <div className="text-display text-2xl text-ink leading-none">{value}</div>
      {hint && <div className="text-xs text-muted mt-1.5">{hint}</div>}
    </div>
  );
}

export function AnalyticsSection() {
  const { data } = useSWR<Analytics>("/api/v1/analytics?days=30", {
    refreshInterval: 60_000,
  });

  if (!data) {
    return (
      <div className="bg-paper border border-border rounded-sm p-5">
        <SkeletonRows rows={4} />
      </div>
    );
  }

  const funnelTop = data.funnel[0]?.count || 0;
  const totalHealth = Object.values(data.email_health).reduce((a, b) => a + b, 0);

  return (
    <div className="space-y-3">
      {/* KPI row */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
        <Kpi
          icon={<Reply size={13} />}
          label="Reply rate"
          value={pct(data.reply_rate)}
          hint={`${formatNumber(data.replied_leads)} of ${formatNumber(data.contacted_leads)} contacted`}
        />
        <Kpi
          icon={<Sparkles size={13} />}
          label="Interested"
          value={pct(data.interested_rate)}
          hint={`${formatNumber(data.interested_replies)} warm repl${data.interested_replies === 1 ? "y" : "ies"}`}
        />
        <Kpi
          icon={<CalendarCheck size={13} />}
          label="Meetings"
          value={formatNumber(data.meetings)}
          hint="Booked / requested"
        />
        <Kpi
          icon={<Send size={13} />}
          label="Emails sent"
          value={formatNumber(data.emails_sent)}
          hint={`${formatNumber(data.replies_total)} repl${data.replies_total === 1 ? "y" : "ies"} in`}
        />
        <Kpi
          icon={<ShieldCheck size={13} />}
          label="Sendable"
          value={formatNumber(data.sendable_leads)}
          hint="Verified emails"
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        {/* Conversion funnel */}
        <div className="bg-paper border border-border rounded-sm p-5">
          <div className="text-display text-xl mb-1">Conversion funnel</div>
          <div className="text-xs text-muted mb-4">Lead journey, all-time</div>
          {funnelTop === 0 ? (
            <div className="py-6 text-muted text-sm">No leads yet.</div>
          ) : (
            <div className="space-y-2.5">
              {data.funnel.map((stage, i) => {
                const prev = i > 0 ? data.funnel[i - 1].count : stage.count;
                const widthPct = funnelTop ? (stage.count / funnelTop) * 100 : 0;
                const convPct = prev ? (stage.count / prev) * 100 : 0;
                return (
                  <div key={stage.key}>
                    <div className="flex items-center justify-between text-xs mb-1">
                      <span className="text-ink">{stage.label}</span>
                      <span className="text-mono text-muted">
                        {formatNumber(stage.count)}
                        {i > 0 && (
                          <span className="ml-1.5 text-[10px]">
                            {convPct.toFixed(0)}%
                          </span>
                        )}
                      </span>
                    </div>
                    <div className="h-2 bg-terracotta-wash rounded-sm overflow-hidden">
                      <div
                        className="h-full bg-terracotta rounded-sm transition-all"
                        style={{ width: `${Math.max(widthPct, stage.count > 0 ? 3 : 0)}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* 30-day trend */}
        <div className="lg:col-span-2 bg-paper border border-border rounded-sm p-5">
          <div className="flex items-end justify-between mb-4">
            <div>
              <div className="text-display text-xl">Activity — last 30 days</div>
              <div className="text-xs text-muted mt-0.5">
                Emails sent vs replies received
              </div>
            </div>
            <div className="flex items-center gap-3 text-[11px] text-muted">
              <span className="flex items-center gap-1">
                <span className="w-2.5 h-2.5 rounded-sm bg-terracotta inline-block" />
                Sent
              </span>
              <span className="flex items-center gap-1">
                <span className="w-2.5 h-2.5 rounded-full bg-green inline-block" />
                Replies
              </span>
            </div>
          </div>
          <div className="h-52">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart
                data={data.trend}
                margin={{ top: 4, right: 4, left: -22, bottom: 0 }}
              >
                <CartesianGrid
                  vertical={false}
                  stroke="var(--border)"
                  strokeDasharray="2 4"
                />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 10, fill: "var(--muted)" }}
                  tickFormatter={(d: string) => d.slice(5)}
                  interval={6}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  tick={{ fontSize: 10, fill: "var(--muted)" }}
                  allowDecimals={false}
                  axisLine={false}
                  tickLine={false}
                  width={32}
                />
                <Tooltip
                  contentStyle={{
                    fontSize: 12,
                    borderRadius: 4,
                    border: "1px solid var(--border)",
                    background: "var(--paper)",
                  }}
                />
                <Bar
                  dataKey="sent"
                  fill="var(--terracotta)"
                  radius={[2, 2, 0, 0]}
                  maxBarSize={14}
                  name="Sent"
                />
                <Line
                  type="monotone"
                  dataKey="replies"
                  stroke="var(--green)"
                  strokeWidth={2}
                  dot={false}
                  name="Replies"
                />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {/* Email health bar */}
      {totalHealth > 0 && (
        <div className="bg-paper border border-border rounded-sm p-5">
          <div className="flex items-end justify-between mb-3">
            <div>
              <div className="text-display text-xl">Email health</div>
              <div className="text-xs text-muted mt-0.5">
                Only verified emails are sent — the rest are held to protect deliverability
              </div>
            </div>
            <span className="text-mono text-xs text-muted">
              {formatNumber(data.email_health.verified ?? 0)} / {formatNumber(totalHealth)} sendable
            </span>
          </div>
          <div className="flex h-2.5 rounded-sm overflow-hidden bg-border mb-3">
            {HEALTH_ORDER.map((k) =>
              data.email_health[k] ? (
                <div
                  key={k}
                  style={{
                    width: `${(data.email_health[k] / totalHealth) * 100}%`,
                    background: HEALTH_COLOR[k],
                  }}
                  title={`${HEALTH_LABEL[k]}: ${data.email_health[k]}`}
                />
              ) : null,
            )}
          </div>
          <div className="flex flex-wrap gap-x-5 gap-y-1.5">
            {HEALTH_ORDER.map((k) =>
              data.email_health[k] ? (
                <span key={k} className="flex items-center gap-1.5 text-xs text-muted">
                  <span
                    className="w-2.5 h-2.5 rounded-sm inline-block"
                    style={{ background: HEALTH_COLOR[k] }}
                  />
                  {HEALTH_LABEL[k]}
                  <span className="text-mono text-ink">{formatNumber(data.email_health[k])}</span>
                </span>
              ) : null,
            )}
          </div>
        </div>
      )}
    </div>
  );
}
