"use client";

import useSWR from "swr";
import { useState } from "react";
import { Play, Eye } from "lucide-react";
import { PageHeader } from "../_components/header";
import { Button } from "@/components/ui/button";
import { SkeletonRows } from "@/components/ui/skeleton";
import { ErrorCard } from "@/components/ui/error-card";
import { EmptyState } from "@/components/ui/empty-state";
import { useToast } from "@/components/ui/toast";
import { StatCard } from "@/components/ui/stat-card";
import { apiFetch } from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";

type StepResult = {
  lead_id: string;
  step_number: number;
  action: string;
  reason: string;
  reason_human?: string;
  touch_id: string | null;
  defer_until: string | null;
};

type SequenceRun = {
  processed: number;
  sent: number;
  queued_linkedin: number;
  deferred: number;
  skipped: number;
  errors: number;
  stale: number;
  pending_approval: number;
  results: StepResult[];
};

const ACTION_TONE: Record<string, string> = {
  sent: "text-green",
  queued_linkedin: "text-warning",
  deferred: "text-muted",
  skipped: "text-muted",
  error: "text-terracotta",
  stale: "text-muted",
};

export default function SequencePage() {
  const { data: preview, error, mutate, isLoading } = useSWR<SequenceRun>(
    "/api/v1/sequence/preview",
  );
  const [lastRun, setLastRun] = useState<SequenceRun | null>(null);
  const [running, setRunning] = useState(false);
  const toast = useToast();

  async function runReal() {
    setRunning(true);
    try {
      const res = await apiFetch<SequenceRun>("/api/v1/sequence/run", {
        method: "POST",
      });
      setLastRun(res);
      toast.push({
        title: `Sequence ran · ${res.sent} sent`,
        description: `${res.queued_linkedin} LinkedIn, ${res.deferred} deferred, ${res.errors} errors`,
        tone: res.errors > 0 ? "error" : "success",
      });
      mutate();
    } catch (e) {
      toast.push({
        title: "Sequence failed",
        description: e instanceof Error ? e.message : String(e),
        tone: "error",
      });
    } finally {
      setRunning(false);
    }
  }

  const display = lastRun ?? preview ?? null;

  return (
    <>
      <PageHeader
        title="Sequence"
        description="Dry-run shows what would happen. Click Run to send."
        actions={
          <>
            <Button variant="ghost" onClick={() => mutate()} disabled={isLoading}>
              <Eye size={14} />
              Refresh preview
            </Button>
            <Button variant="primary" onClick={runReal} disabled={running}>
              <Play size={14} />
              {running ? "Running…" : "Run sequence"}
            </Button>
          </>
        }
      />

      <div className="px-8 py-6 space-y-6">
        {error ? (
          <ErrorCard message={error?.message} onRetry={() => mutate()} />
        ) : isLoading || !display ? (
          <SkeletonRows rows={6} />
        ) : (
          <>
            <div className="text-xs text-muted">
              {lastRun ? "Last run" : "Dry-run preview"} ·{" "}
              <span className="text-mono">{formatDateTime(new Date().toISOString())}</span>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3">
              <StatCard label="Processed" value={formatNumber(display.processed)} />
              <StatCard label="Sent" value={formatNumber(display.sent)} accent={display.sent > 0 ? "green" : null} />
              <StatCard label="LinkedIn" value={formatNumber(display.queued_linkedin)} accent={display.queued_linkedin > 0 ? "warning" : null} />
              <StatCard label="Deferred" value={formatNumber(display.deferred)} />
              <StatCard label="Skipped" value={formatNumber(display.skipped)} />
              <StatCard label="Errors" value={formatNumber(display.errors)} accent={display.errors > 0 ? "terracotta" : null} />
              <StatCard label="Pending approval" value={formatNumber(display.pending_approval)} />
            </div>

            {display.results.length === 0 ? (
              <EmptyState
                title="Nothing in the queue"
                description="The sequencer found no leads to act on. Import or approve leads to fill the pipeline."
              />
            ) : (
              <div className="bg-paper border border-border rounded-sm overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="border-b border-border bg-cream">
                    <tr className="text-left text-[10px] uppercase tracking-wider text-muted">
                      <th className="px-3 py-2">Lead</th>
                      <th className="px-3 py-2 w-16">Step</th>
                      <th className="px-3 py-2 w-28">Action</th>
                      <th className="px-3 py-2">Reason</th>
                      <th className="px-3 py-2 w-32">Defer until</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {display.results.map((r, i) => (
                      <tr key={i}>
                        <td className="px-3 py-2 text-mono text-xs text-ink/80">
                          {r.lead_id.slice(0, 8)}…
                        </td>
                        <td className="px-3 py-2">{r.step_number}</td>
                        <td className={`px-3 py-2 font-medium ${ACTION_TONE[r.action] ?? "text-ink"}`}>
                          {r.action}
                        </td>
                        <td className="px-3 py-2 text-xs text-muted">{r.reason_human || r.reason}</td>
                        <td className="px-3 py-2 text-xs text-muted">
                          {r.defer_until ? formatDateTime(r.defer_until) : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>
    </>
  );
}
