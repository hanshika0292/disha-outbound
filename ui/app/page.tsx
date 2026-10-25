"use client";

import useSWR from "swr";
import Link from "next/link";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Cell,
  Tooltip,
} from "recharts";
import {
  PenLine,
  Users,
  Send,
  Handshake,
  AlertCircle,
  DollarSign,
  ArrowRight,
  Activity,
  CheckCircle2,
  Clock,
  MessageSquare,
} from "lucide-react";
import { PageHeader } from "./_components/header";
import { OnboardingChecklist } from "./_components/onboarding";
import { Worklist } from "./_components/worklist";
import { ActivityFeed } from "./_components/activity-feed";
import { AnalyticsSection } from "./_components/analytics";
import { StatCard } from "@/components/ui/stat-card";
import { SkeletonCard, SkeletonRows } from "@/components/ui/skeleton";
import { ErrorCard } from "@/components/ui/error-card";
import { EmptyState } from "@/components/ui/empty-state";
import {
  formatCurrency,
  formatNumber,
  relativeTime,
} from "@/lib/format";

type Dashboard = {
  pipeline: {
    by_state: Record<string, number>;
    by_tier: Record<string, number>;
    pending_approval: number;
    pending_draft_review: number;
    active_in_sequence: number;
  };
  today: {
    touches_sent: number;
    leads_sourced: number;
    leads_scored: number;
    replies_received: number;
    llm_calls: number;
    llm_cost_usd: string;
  };
  pending_approvals: number;
  pending_drafts: number;
  pending_linkedin: number;
  recent_events: Array<{
    id: string;
    event_type: string;
    entity_type: string;
    entity_id: string;
    payload: Record<string, unknown>;
    created_at: string;
  }>;
};

type CostForecast = {
  observed_days: number;
  observed_calls: number;
  observed_total_usd: string;
  avg_daily_cost_usd: string;
  projected_weekly_at_n_leads: Record<string, string>;
  projected_monthly_at_n_leads: Record<string, string>;
};

type Settings = {
  config?: Record<string, string | boolean | null>;
  [key: string]: unknown;
};

const STATE_ORDER = [
  "new",
  "scored",
  "approved",
  "in_sequence",
  "paused",
  "replied",
  "meeting_booked",
  "closed_won",
  "closed_lost",
];

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
};

const EVENT_ICONS: Record<string, React.ReactNode> = {
  lead: <Users size={12} />,
  draft: <PenLine size={12} />,
  touch: <Send size={12} />,
  reply: <MessageSquare size={12} />,
  sequence: <Activity size={12} />,
  approved: <CheckCircle2 size={12} />,
};

function eventIcon(eventType: string): React.ReactNode {
  const first = eventType.split(".")[0] ?? "";
  return EVENT_ICONS[first] ?? <Clock size={12} />;
}

export default function DashboardPage() {
  const { data, error, mutate, isLoading } = useSWR<Dashboard>(
    "/api/v1/dashboard",
    {
      refreshInterval: 30_000,
    },
  );
  const { data: cost } = useSWR<CostForecast>("/api/v1/reports/cost?days=7");
  const { data: settings } = useSWR<Settings>("/api/v1/settings");

  // Determine if this is a new user (no leads at all)
  const totalLeads = data
    ? Object.values(data.pipeline.by_state).reduce((a, b) => a + b, 0)
    : null;
  const isNewUser = totalLeads === 0 && !isLoading && !error;

  // Onboarding state checks. Settings come back as { config: { sender_name } },
  // not flat — read the nested path or Step 1 never marks complete.
  const senderConfigured = Boolean(settings?.config?.sender_name);
  const leadsFound = totalLeads !== null && totalLeads > 0;
  const leadApproved =
    data !== undefined &&
    ((data.pipeline.by_state["approved"] ?? 0) > 0 ||
      (data.pipeline.by_state["in_sequence"] ?? 0) > 0 ||
      (data.pipeline.by_state["replied"] ?? 0) > 0 ||
      (data.pipeline.by_state["meeting_booked"] ?? 0) > 0);

  return (
    <>
      {!isNewUser && (
        <PageHeader
          title="Dashboard"
          description="What needs your attention today."
        />
      )}

      {/* ── New user: full-screen onboarding (persona + checklist) ── */}
      {isNewUser ? (
        <div className="px-8 py-12 min-h-[calc(100vh-0px)]">
          <OnboardingChecklist
            senderConfigured={senderConfigured}
            leadsFound={leadsFound}
            leadApproved={leadApproved}
          />
        </div>
      ) : (
        /* ── Returning user: full dashboard ── */
        <div className="px-8 py-6 space-y-6 page-enter">
          {/* What needs you today */}
          <Worklist />

          {/* Row 1 — stat cards */}
          {error ? (
            <ErrorCard
              message={error?.message ?? "Couldn't load dashboard"}
              onRetry={() => mutate()}
            />
          ) : isLoading || !data ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
              {Array.from({ length: 5 }).map((_, i) => (
                <SkeletonCard key={i} />
              ))}
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
              <StatCard
                label="Leads to review"
                value={formatNumber(data.pending_approvals)}
                hint="Scored, awaiting approval"
                accent={data.pending_approvals > 0 ? "terracotta" : null}
                icon={<Users size={14} />}
                href="/leads?state=scored"
              />
              <StatCard
                label="Drafts to approve"
                value={formatNumber(data.pending_drafts)}
                hint="Pending your review"
                accent={data.pending_drafts > 0 ? "terracotta" : null}
                icon={<PenLine size={14} />}
                href="/drafts"
              />
              <StatCard
                label="LinkedIn queue"
                value={formatNumber(data.pending_linkedin)}
                hint="Manual sends needed"
                accent={data.pending_linkedin > 0 ? "warning" : null}
                icon={<Handshake size={14} />}
                href="/linkedin"
              />
              <StatCard
                label="Touches sent today"
                value={formatNumber(data.today.touches_sent)}
                hint={`${formatNumber(data.today.replies_received)} repl${data.today.replies_received === 1 ? "y" : "ies"} in`}
                accent={data.today.touches_sent > 0 ? "green" : null}
                icon={<Send size={14} />}
              />
              <StatCard
                label="LLM cost today"
                value={formatCurrency(Number(data.today.llm_cost_usd))}
                hint={`${formatNumber(data.today.llm_calls)} calls`}
                icon={<DollarSign size={14} />}
              />
            </div>
          )}

          {/* Performance analytics — funnel, KPIs, trend, email health */}
          <AnalyticsSection />

          {/* Row 2 — pipeline + events */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
            {/* Pipeline chart */}
            <div className="lg:col-span-2 bg-paper border border-border rounded-sm p-5">
              <div className="flex items-end justify-between mb-5">
                <div>
                  <div className="text-display text-xl">Pipeline</div>
                  <div className="text-xs text-muted mt-0.5">
                    Leads by state, all-time
                  </div>
                </div>
                {data && (
                  <Link
                    href="/leads"
                    className="text-xs text-muted hover:text-terracotta transition-colors flex items-center gap-1"
                  >
                    View all
                    <ArrowRight size={11} />
                  </Link>
                )}
              </div>
              {isLoading || !data ? (
                <div className="h-52">
                  <SkeletonRows rows={3} />
                </div>
              ) : Object.keys(data.pipeline.by_state).length === 0 ? (
                <div className="py-8 text-muted text-sm">
                  No leads yet. Import a CSV from the Leads page to get started.
                </div>
              ) : (
                <PipelineChart by_state={data.pipeline.by_state} />
              )}
            </div>

            {/* Activity feed — plain-English log of what the machine did */}
            <ActivityFeed />
          </div>

          {/* Row 3 — attention + cost */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
            {/* Needs attention */}
            <div className="lg:col-span-2 bg-paper border border-border rounded-sm p-5">
              <div className="text-display text-xl mb-4">Needs attention</div>
              <AttentionList data={data} />
            </div>

            {/* 7-day cost */}
            <div className="bg-paper border border-border rounded-sm p-5">
              <div className="text-display text-xl mb-4">7-day cost</div>
              {cost ? (
                <div className="space-y-4">
                  <div>
                    <div className="text-display text-3xl text-ink">
                      {formatCurrency(Number(cost.observed_total_usd))}
                    </div>
                    <div className="text-xs text-muted mt-1">
                      {formatNumber(cost.observed_calls)} LLM calls ·{" "}
                      avg {formatCurrency(Number(cost.avg_daily_cost_usd))}/day
                    </div>
                  </div>
                  <div className="border-t border-border pt-3">
                    <div className="text-[11px] uppercase tracking-wider text-muted mb-2">
                      Projected weekly
                    </div>
                    <table className="w-full text-sm">
                      <tbody>
                        {Object.entries(cost.projected_weekly_at_n_leads).map(
                          ([n, v]) => (
                            <tr
                              key={n}
                              className="border-b border-border last:border-0"
                            >
                              <td className="py-1.5 text-xs text-muted">
                                @ {n} leads/wk
                              </td>
                              <td className="py-1.5 text-right text-mono text-xs">
                                {formatCurrency(Number(v))}
                              </td>
                            </tr>
                          ),
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>
              ) : (
                <SkeletonRows rows={5} />
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function PipelineChart({ by_state }: { by_state: Record<string, number> }) {
  const ordered = STATE_ORDER.filter((s) => (by_state[s] ?? 0) > 0).concat(
    Object.keys(by_state).filter((s) => !STATE_ORDER.includes(s)),
  );
  const data = ordered.map((s) => ({
    state: STATE_LABEL[s] ?? s.replace(/_/g, " "),
    count: by_state[s] ?? 0,
    fill: colorForState(s),
  }));
  return (
    <div className="h-56">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 8, left: 0 }}>
          <XAxis
            dataKey="state"
            tick={{ fontSize: 11, fill: "#64748b" }}
            tickLine={false}
            axisLine={{ stroke: "#e9edf2" }}
            interval={0}
          />
          <YAxis
            tick={{ fontSize: 11, fill: "#64748b" }}
            tickLine={false}
            axisLine={{ stroke: "#e9edf2" }}
            allowDecimals={false}
          />
          <Tooltip
            cursor={{ fill: "#eef2ff" }}
            contentStyle={{
              background: "#fff",
              border: "1px solid #d4c4ae",
              borderRadius: 2,
              fontSize: 12,
            }}
          />
          <Bar dataKey="count" radius={[2, 2, 0, 0]}>
            {data.map((entry, i) => (
              <Cell key={i} fill={entry.fill} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function colorForState(state: string): string {
  if (state === "meeting_booked" || state === "closed_won") return "#059669";
  if (state === "replied" || state === "in_sequence") return "#34d399";
  if (state === "approved" || state === "scored") return "#475569";
  if (state === "paused") return "#d97706";
  return "#94a3b8";
}

function humanizeEvent(t: string): string {
  return t.replace(/\./g, " · ").replace(/_/g, " ");
}

function AttentionList({ data }: { data: Dashboard | undefined }) {
  if (!data) return <SkeletonRows rows={4} />;
  const rows: Array<{ label: string; count: number; href: string; accent: string }> = [
    {
      label: "Leads scored, awaiting approval",
      count: data.pending_approvals,
      href: "/leads?state=scored",
      accent: "terracotta",
    },
    {
      label: "Drafts pending your approval",
      count: data.pending_drafts,
      href: "/drafts",
      accent: "terracotta",
    },
    {
      label: "LinkedIn sends to do manually",
      count: data.pending_linkedin,
      href: "/linkedin",
      accent: "warning",
    },
  ];
  const total = rows.reduce((acc, r) => acc + r.count, 0);
  if (total === 0) {
    return (
      <EmptyState
        title="All clear"
        description="Nothing needs your attention right now. The sequence will keep running in the background."
        icon={<AlertCircle size={20} />}
      />
    );
  }
  return (
    <ul className="divide-y divide-border">
      {rows.map((r) =>
        r.count > 0 ? (
          <li key={r.href} className="flex items-center justify-between py-3.5 group">
            <Link
              href={r.href}
              className="text-sm text-ink hover:text-terracotta transition-colors flex items-center gap-2"
            >
              <ArrowRight
                size={13}
                className="text-muted group-hover:text-terracotta transition-colors shrink-0"
              />
              {r.label}
            </Link>
            <span
              className={`text-display text-2xl ${
                r.accent === "warning" ? "text-warning" : "text-terracotta"
              }`}
            >
              {r.count}
            </span>
          </li>
        ) : null,
      )}
    </ul>
  );
}
