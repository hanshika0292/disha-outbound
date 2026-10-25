"use client";

import useSWR from "swr";
import { useState, useMemo } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import {
  Upload,
  Plus,
  RefreshCcw,
  Check,
  X,
  Search,
  FileSpreadsheet,
  UserPlus,
  Sparkles,
} from "lucide-react";
import { PageHeader } from "../_components/header";
import { Button } from "@/components/ui/button";
import { Skeleton, SkeletonRows } from "@/components/ui/skeleton";
import { ErrorCard } from "@/components/ui/error-card";
import { EmptyState } from "@/components/ui/empty-state";
import { TierBadge, StateBadge } from "@/components/ui/badges";
import { ConfirmPopover } from "@/components/ui/confirm-popover";
import { useToast } from "@/components/ui/toast";
import { apiFetch } from "@/lib/api";
import { formatNumber, relativeTime } from "@/lib/format";
import { cn } from "@/lib/cn";
import { LeadDrawer } from "./_lead-drawer";
import { ImportModal } from "./_import-modal";
import { AddLeadModal } from "./_add-lead-modal";
import { FindLeadsModal } from "./_find-leads-modal";

type LeadListResponse = {
  items: Array<{
    lead_id: string;
    first_name: string | null;
    last_name: string | null;
    title: string | null;
    company: string;
    country: string;
    tier: string | null;
    score: number | null;
    state: string;
    last_touch_at: string | null;
  }>;
  total: number;
  page: number;
  page_size: number;
  counts_by_state: Record<string, number>;
};

const TABS: { key: string; label: string }[] = [
  { key: "all", label: "All" },
  { key: "new", label: "New" },
  { key: "scored", label: "Scored" },
  { key: "approved", label: "Approved" },
  { key: "in_sequence", label: "In sequence" },
  { key: "paused", label: "Paused" },
  { key: "replied", label: "Replied" },
  { key: "meeting_booked", label: "Booked" },
];

export default function LeadsPage() {
  const params = useSearchParams();
  const router = useRouter();
  const stateFilter = params.get("state") ?? "all";
  const tierFilter = params.get("tier") ?? "";
  const page = Number(params.get("page") ?? "1");
  const drawerLeadId = params.get("lead");

  const [showImport, setShowImport] = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const [showFind, setShowFind] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const toast = useToast();

  const query = new URLSearchParams();
  if (stateFilter !== "all") query.set("state", stateFilter);
  if (tierFilter) query.set("tier", tierFilter);
  query.set("page", String(page));
  query.set("page_size", "25");

  const swrKey = `/api/v1/leads?${query.toString()}`;
  const { data, error, mutate, isLoading } = useSWR<LeadListResponse>(swrKey);

  function setQuery(updates: Record<string, string | null>) {
    const sp = new URLSearchParams(params.toString());
    for (const [k, v] of Object.entries(updates)) {
      if (v === null || v === "") sp.delete(k);
      else sp.set(k, v);
    }
    router.replace(`/leads?${sp.toString()}`);
  }

  async function approve(leadId: string) {
    try {
      await apiFetch(`/api/v1/leads/${leadId}/approve`, { method: "POST" });
      toast.push({ title: "Lead approved", tone: "success" });
      mutate();
    } catch (e) {
      toast.push({
        title: "Approve failed",
        description: e instanceof Error ? e.message : String(e),
        tone: "error",
      });
    }
  }

  async function reject(leadId: string) {
    try {
      await apiFetch(`/api/v1/leads/${leadId}/reject`, {
        method: "POST",
        body: { reason: "manual_reject" },
      });
      toast.push({ title: "Lead rejected", tone: "neutral" });
      mutate();
    } catch (e) {
      toast.push({
        title: "Reject failed",
        description: e instanceof Error ? e.message : String(e),
        tone: "error",
      });
    }
  }

  async function rescore(leadId: string) {
    try {
      await apiFetch(`/api/v1/leads/${leadId}/score`, { method: "POST" });
      toast.push({ title: "Re-scored", tone: "success" });
      mutate();
    } catch (e) {
      toast.push({
        title: "Score failed",
        description: e instanceof Error ? e.message : String(e),
        tone: "error",
      });
    }
  }

  // Local search filter — applied on top of the paginated API result
  const filteredItems = useMemo(() => {
    if (!data?.items) return [];
    if (!searchQuery.trim()) return data.items;
    const q = searchQuery.toLowerCase();
    return data.items.filter((it) => {
      const name = `${it.first_name ?? ""} ${it.last_name ?? ""}`.toLowerCase();
      return (
        name.includes(q) ||
        it.company.toLowerCase().includes(q) ||
        (it.title ?? "").toLowerCase().includes(q) ||
        (it.country ?? "").toLowerCase().includes(q)
      );
    });
  }, [data?.items, searchQuery]);

  return (
    <>
      <PageHeader
        title="Leads"
        description="Everyone who's been imported. Approve to start the sequence."
        actions={
          <>
            <Button variant="secondary" onClick={() => setShowImport(true)}>
              <Upload size={14} />
              Import CSV
            </Button>
            <Button variant="secondary" onClick={() => setShowAdd(true)}>
              <Plus size={14} />
              Add lead
            </Button>
            <Button variant="primary" onClick={() => setShowFind(true)}>
              <Search size={14} />
              Find leads
            </Button>
          </>
        }
      />

      <div className="px-8 py-5 space-y-4">
        {/* Search bar */}
        <div className="relative max-w-sm">
          <Search
            size={14}
            className="absolute left-3 top-1/2 -translate-y-1/2 text-muted pointer-events-none"
          />
          <input
            type="text"
            placeholder="Search by name, company, role…"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className={cn(
              "w-full pl-9 pr-3 h-9 text-sm border border-border rounded-sm bg-paper",
              "placeholder:text-muted/60 focus:outline-none focus:border-terracotta/50",
              "transition-colors",
            )}
          />
          {searchQuery && (
            <button
              onClick={() => setSearchQuery("")}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-muted hover:text-ink transition-colors"
            >
              <X size={12} />
            </button>
          )}
        </div>

        {/* Tabs */}
        <div className="flex items-center justify-between gap-3 border-b border-border overflow-x-auto scrollbar-thin">
          <div className="flex gap-1">
            {TABS.map((t) => {
              const active = stateFilter === t.key;
              const count =
                t.key === "all"
                  ? data?.total ?? 0
                  : data?.counts_by_state[t.key] ?? 0;
              return (
                <button
                  key={t.key}
                  onClick={() =>
                    setQuery({
                      state: t.key === "all" ? null : t.key,
                      page: null,
                    })
                  }
                  className={cn(
                    "px-3 py-2 text-sm border-b-2 -mb-px transition-colors",
                    active
                      ? "border-terracotta text-terracotta font-medium"
                      : "border-transparent text-muted hover:text-ink",
                  )}
                >
                  {t.label}
                  <span
                    className={cn(
                      "ml-1.5 text-[10px] tabular-nums",
                      active ? "text-terracotta" : "text-muted/70",
                    )}
                  >
                    {formatNumber(count)}
                  </span>
                </button>
              );
            })}
          </div>
          <div className="flex items-center gap-1 pr-2">
            {(["", "A", "B", "C", "D"] as const).map((t) => (
              <button
                key={t || "any"}
                onClick={() => setQuery({ tier: t || null, page: null })}
                className={cn(
                  "px-2 py-1 text-xs rounded-sm border",
                  tierFilter === t
                    ? "bg-ink text-cream border-ink"
                    : "border-border text-muted hover:text-ink",
                )}
              >
                {t || "Any tier"}
              </button>
            ))}
          </div>
        </div>

        {/* Table */}
        {error ? (
          <ErrorCard
            message={error?.message ?? "Couldn't load leads"}
            onRetry={() => mutate()}
          />
        ) : isLoading || !data ? (
          <SkeletonRows rows={8} />
        ) : data.items.length === 0 ? (
          /* No leads at all — rich empty state with 3 action cards */
          <EmptyState
            title="No leads yet"
            description="Add leads by importing a CSV, adding one manually, or letting Disha find them for you."
            actions={[
              <ActionCard
                key="csv"
                icon={<FileSpreadsheet size={22} />}
                title="Import CSV"
                description="Upload a spreadsheet of contacts"
                onClick={() => setShowImport(true)}
              />,
              <ActionCard
                key="add"
                icon={<UserPlus size={22} />}
                title="Add manually"
                description="Enter a single lead by hand"
                onClick={() => setShowAdd(true)}
              />,
              <ActionCard
                key="find"
                icon={<Sparkles size={22} />}
                title="Find with AI"
                description="Describe your ICP and let Disha search"
                onClick={() => setShowFind(true)}
                primary
              />,
            ]}
          />
        ) : filteredItems.length === 0 ? (
          /* Search returned no results */
          <div className="py-12 text-center text-muted text-sm">
            No leads match &ldquo;{searchQuery}&rdquo;.{" "}
            <button
              onClick={() => setSearchQuery("")}
              className="underline underline-offset-2 hover:text-ink transition-colors"
            >
              Clear search
            </button>
          </div>
        ) : (
          <div className="bg-paper border border-border rounded-sm overflow-hidden">
            <table className="w-full text-sm">
              <thead className="border-b border-border bg-cream">
                <tr className="text-left text-[10px] uppercase tracking-wider text-muted">
                  <th className="px-3 py-2.5 w-10">Tier</th>
                  <th className="px-3 py-2.5">Name</th>
                  <th className="px-3 py-2.5">Role</th>
                  <th className="px-3 py-2.5">Company</th>
                  <th className="px-3 py-2.5 w-16">Score</th>
                  <th className="px-3 py-2.5 w-28">State</th>
                  <th className="px-3 py-2.5 w-28">Last touch</th>
                  <th className="px-3 py-2.5 w-32 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {filteredItems.map((it) => {
                  const name =
                    `${it.first_name ?? ""} ${it.last_name ?? ""}`.trim() ||
                    "—";
                  return (
                    <tr
                      key={it.lead_id}
                      className="hover:bg-cream/60 cursor-pointer transition-colors"
                      onClick={() => setQuery({ lead: it.lead_id })}
                    >
                      <td className="px-3 py-3">
                        <TierBadge tier={it.tier} />
                      </td>
                      <td className="px-3 py-3 font-medium text-ink">{name}</td>
                      <td className="px-3 py-3 text-ink/80">
                        {it.title || "—"}
                      </td>
                      <td className="px-3 py-3 text-ink/80">
                        {it.company}
                        {it.country ? (
                          <span className="text-muted text-xs ml-1.5">
                            {it.country}
                          </span>
                        ) : null}
                      </td>
                      <td className="px-3 py-3 text-mono text-xs">
                        {it.score !== null ? it.score.toFixed(1) : "—"}
                      </td>
                      <td className="px-3 py-3">
                        <StateBadge state={it.state} />
                      </td>
                      <td className="px-3 py-3 text-xs text-muted">
                        {relativeTime(it.last_touch_at)}
                      </td>
                      <td
                        className="px-3 py-3 text-right"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <div className="inline-flex items-center gap-1">
                          {it.state === "scored" ? (
                            <>
                              <Button
                                size="sm"
                                variant="primary"
                                onClick={() => approve(it.lead_id)}
                                title="Approve"
                              >
                                <Check size={12} />
                              </Button>
                              <ConfirmPopover
                                trigger={
                                  <Button
                                    size="sm"
                                    variant="danger"
                                    title="Reject"
                                  >
                                    <X size={12} />
                                  </Button>
                                }
                                title="Reject this lead?"
                                description="They'll be marked rejected and won't enter the sequence."
                                confirmLabel="Reject"
                                destructive
                                onConfirm={() => reject(it.lead_id)}
                              />
                            </>
                          ) : null}
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => rescore(it.lead_id)}
                            title="Re-score"
                          >
                            <RefreshCcw size={12} />
                          </Button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Pagination */}
        {data && data.total > data.page_size ? (
          <Pagination
            total={data.total}
            page={data.page}
            pageSize={data.page_size}
            onChange={(p) => setQuery({ page: String(p) })}
          />
        ) : null}
      </div>

      {/* Modals */}
      <ImportModal
        open={showImport}
        onOpenChange={setShowImport}
        onSuccess={() => mutate()}
      />
      <AddLeadModal
        open={showAdd}
        onOpenChange={setShowAdd}
        onSuccess={() => mutate()}
      />
      <FindLeadsModal
        open={showFind}
        onOpenChange={setShowFind}
        onSuccess={() => mutate()}
      />

      {/* Drawer */}
      <LeadDrawer
        leadId={drawerLeadId}
        onClose={() => setQuery({ lead: null })}
        onMutated={() => mutate()}
      />
    </>
  );
}

/** Small action card used in the empty state. */
function ActionCard({
  icon,
  title,
  description,
  onClick,
  primary = false,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
  onClick: () => void;
  primary?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "w-full h-full flex flex-col items-center gap-2.5 p-5 rounded-sm border text-center",
        "transition-colors cursor-pointer",
        primary
          ? "bg-terracotta-wash/40 border-terracotta/30 hover:border-terracotta/60 hover:bg-terracotta-wash/60"
          : "bg-paper border-border hover:border-border-strong hover:bg-cream/60",
      )}
    >
      <span
        className={cn(
          "mt-1",
          primary ? "text-terracotta" : "text-muted",
        )}
      >
        {icon}
      </span>
      <div>
        <div className="text-sm font-medium text-ink">{title}</div>
        <div className="text-xs text-muted mt-0.5 leading-relaxed">
          {description}
        </div>
      </div>
    </button>
  );
}

function Pagination({
  total,
  page,
  pageSize,
  onChange,
}: {
  total: number;
  page: number;
  pageSize: number;
  onChange: (p: number) => void;
}) {
  const maxPage = Math.max(1, Math.ceil(total / pageSize));
  return (
    <div className="flex items-center justify-between text-xs text-muted">
      <div>
        Page {page} of {maxPage} · {formatNumber(total)} leads
      </div>
      <div className="flex items-center gap-1.5">
        <Button
          size="sm"
          variant="ghost"
          disabled={page <= 1}
          onClick={() => onChange(page - 1)}
        >
          Prev
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={page >= maxPage}
          onClick={() => onChange(page + 1)}
        >
          Next
        </Button>
      </div>
    </div>
  );
}

// Keep Skeleton import linked to silence unused-warning if branches change.
void Skeleton;
