"use client";

import useSWR from "swr";
import { PageHeader } from "../_components/header";
import { StatCard } from "@/components/ui/stat-card";
import { SkeletonRows, SkeletonCard } from "@/components/ui/skeleton";
import { ErrorCard } from "@/components/ui/error-card";
import { formatCurrency, formatNumber, formatPercent } from "@/lib/format";

type Weekly = {
  activity: {
    period_start: string;
    period_end: string;
    leads_sourced: number;
    leads_scored: number;
    leads_approved: number;
    leads_rejected: number;
    leads_entered_sequence: number;
    leads_replied: number;
    leads_booked: number;
    touches_sent: number;
    touches_queued_linkedin: number;
    touches_deferred: number;
    touches_failed: number;
    replies_received: number;
    reply_intent_breakdown: Record<string, number>;
    llm_calls: number;
    llm_cost_usd: string;
    llm_cost_by_purpose: Record<string, string>;
  };
  funnel: {
    sourced_to_scored: number;
    scored_to_approved: number;
    approved_to_in_sequence: number;
    in_sequence_to_replied: number;
    replied_to_booked: number;
    overall_sourced_to_booked: number;
  };
};

type CostForecast = {
  observed_days: number;
  observed_calls: number;
  observed_unique_leads: number;
  observed_total_usd: string;
  avg_daily_cost_usd: string;
  avg_cost_per_lead_lifecycle_usd: string;
  projected_weekly_at_n_leads: Record<string, string>;
  projected_monthly_at_n_leads: Record<string, string>;
  breakdown_by_phase: Record<string, string>;
};

const FUNNEL_STEPS = [
  { key: "sourced_to_scored", label: "Sourced → Scored" },
  { key: "scored_to_approved", label: "Scored → Approved" },
  { key: "approved_to_in_sequence", label: "Approved → In sequence" },
  { key: "in_sequence_to_replied", label: "In sequence → Replied" },
  { key: "replied_to_booked", label: "Replied → Booked" },
] as const;

export default function ReportsPage() {
  const { data: weekly, error: weeklyError, mutate: refetchWeekly } =
    useSWR<Weekly>("/api/v1/reports/weekly");
  const { data: cost, error: costError } =
    useSWR<CostForecast>("/api/v1/reports/cost?days=14");

  return (
    <>
      <PageHeader
        title="Reports"
        description="The last 7 days, the funnel, and the cost."
      />

      <div className="px-8 py-6 space-y-6">
        {weeklyError ? (
          <ErrorCard
            message={weeklyError?.message}
            onRetry={() => refetchWeekly()}
          />
        ) : !weekly ? (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <SkeletonCard key={i} />
            ))}
          </div>
        ) : (
          <>
            {/* week stats */}
            <section>
              <div className="text-display text-xl mb-3">7-day activity</div>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
                <StatCard label="Sourced" value={formatNumber(weekly.activity.leads_sourced)} />
                <StatCard label="Scored" value={formatNumber(weekly.activity.leads_scored)} />
                <StatCard
                  label="Approved"
                  value={formatNumber(weekly.activity.leads_approved)}
                  accent="green"
                />
                <StatCard label="Touches sent" value={formatNumber(weekly.activity.touches_sent)} />
                <StatCard label="Replies" value={formatNumber(weekly.activity.replies_received)} accent={weekly.activity.replies_received > 0 ? "terracotta" : null} />
                <StatCard label="Booked" value={formatNumber(weekly.activity.leads_booked)} accent={weekly.activity.leads_booked > 0 ? "green" : null} />
              </div>
            </section>

            {/* funnel */}
            <section className="bg-paper border border-border rounded-sm p-5">
              <div className="text-display text-xl mb-4">Funnel</div>
              <ul className="space-y-2">
                {FUNNEL_STEPS.map((s) => {
                  const rate = weekly.funnel[s.key] ?? 0;
                  return (
                    <li key={s.key}>
                      <div className="flex items-center justify-between text-sm mb-1">
                        <span className="text-ink/85">{s.label}</span>
                        <span className="text-mono text-xs text-muted">
                          {rate.toFixed(1)}%
                        </span>
                      </div>
                      <div className="h-2 bg-cream border border-border rounded-sm overflow-hidden">
                        <div
                          className="h-full bg-terracotta"
                          style={{ width: `${Math.min(100, rate)}%` }}
                        />
                      </div>
                    </li>
                  );
                })}
              </ul>
              <div className="text-xs text-muted mt-4 pt-3 border-t border-border">
                Overall: sourced → booked is{" "}
                <span className="text-ink font-medium">
                  {formatPercent(weekly.funnel.overall_sourced_to_booked / 100)}
                </span>
              </div>
            </section>

            {/* reply intent */}
            {Object.keys(weekly.activity.reply_intent_breakdown).length > 0 ? (
              <section className="bg-paper border border-border rounded-sm p-5">
                <div className="text-display text-xl mb-3">Reply intent</div>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-sm">
                  {Object.entries(weekly.activity.reply_intent_breakdown).map(
                    ([intent, count]) => (
                      <div
                        key={intent}
                        className="flex items-baseline justify-between border-b border-border py-1.5"
                      >
                        <span className="text-muted text-xs uppercase tracking-wider">
                          {intent}
                        </span>
                        <span className="text-mono text-ink">{count}</span>
                      </div>
                    ),
                  )}
                </div>
              </section>
            ) : null}
          </>
        )}

        {/* cost */}
        <section className="bg-paper border border-border rounded-sm p-5">
          <div className="text-display text-xl mb-3">14-day LLM cost</div>
          {costError ? (
            <ErrorCard message={costError?.message} />
          ) : !cost ? (
            <SkeletonRows rows={4} />
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="space-y-3">
                <div>
                  <div className="text-display text-3xl">
                    {formatCurrency(Number(cost.observed_total_usd))}
                  </div>
                  <div className="text-xs text-muted mt-0.5">
                    over {cost.observed_days} days · {formatNumber(cost.observed_calls)} calls ·{" "}
                    {formatNumber(cost.observed_unique_leads)} unique leads
                  </div>
                </div>
                <div className="text-sm text-muted">
                  Avg per lead lifecycle:{" "}
                  <span className="text-mono text-ink">
                    {formatCurrency(Number(cost.avg_cost_per_lead_lifecycle_usd))}
                  </span>
                </div>
                <div className="border-t border-border pt-3">
                  <div className="text-[11px] uppercase tracking-wider text-muted mb-2">
                    By phase
                  </div>
                  <ul className="space-y-1 text-sm">
                    {Object.entries(cost.breakdown_by_phase).map(([phase, v]) => (
                      <li key={phase} className="flex items-baseline justify-between">
                        <span className="text-ink/85">{phase}</span>
                        <span className="text-mono text-xs">
                          {formatCurrency(Number(v))}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
              <div>
                <div className="text-[11px] uppercase tracking-wider text-muted mb-2">
                  Projected monthly
                </div>
                <table className="w-full text-sm">
                  <tbody>
                    {Object.entries(cost.projected_monthly_at_n_leads).map(
                      ([n, v]) => (
                        <tr
                          key={n}
                          className="border-b border-border last:border-0"
                        >
                          <td className="py-2 text-muted">@ {n} leads/wk</td>
                          <td className="py-2 text-right text-mono">
                            {formatCurrency(Number(v))}
                          </td>
                        </tr>
                      ),
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </section>
      </div>
    </>
  );
}
