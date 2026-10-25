"use client";

import useSWR from "swr";
import { useState } from "react";
import { Drawer } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { TierBadge, StateBadge, ChannelBadge } from "@/components/ui/badges";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorCard } from "@/components/ui/error-card";
import { ConfirmPopover } from "@/components/ui/confirm-popover";
import { useToast } from "@/components/ui/toast";
import { apiFetch } from "@/lib/api";
import { formatDateTime, relativeTime, truncate } from "@/lib/format";
import { Check, X, RefreshCcw, Globe, Sparkles, ExternalLink } from "lucide-react";

type Enrichment = {
  website: string | null;
  company_summary: string;
  why_fit: string[];
  why_now: string;
  hook: string;
  enriched_at: string;
};

type LeadDetail = {
  lead: {
    id: string;
    first_name: string | null;
    last_name: string | null;
    title: string | null;
    linkedin_url: string | null;
    source: string | null;
    notes: string | null;
    email_confidence: string | null;
    email: string | null;
    enrichment: Enrichment | null;
  };
  account: {
    id: string | null;
    name: string | null;
    country: string | null;
    industry: string | null;
    funding_stage: string | null;
    employee_count: number | null;
    website: string | null;
  } | null;
  state: {
    state: string | null;
    tier: string | null;
    score: number | null;
    sequence_started_at: string | null;
    sequence_paused_until: string | null;
  } | null;
  signals: Array<{
    type: string;
    source: string;
    payload: Record<string, unknown>;
    observed_at: string;
  }>;
  touches: Array<{
    id: string;
    step_number: number | null;
    channel: string;
    status: string;
    subject: string | null;
    body: string | null;
    drafted_at: string | null;
    sent_at: string | null;
  }>;
};

export function LeadDrawer({
  leadId,
  onClose,
  onMutated,
}: {
  leadId: string | null;
  onClose: () => void;
  onMutated: () => void;
}) {
  const toast = useToast();
  const [enriching, setEnriching] = useState(false);
  const { data, error, mutate, isLoading } = useSWR<LeadDetail>(
    leadId ? `/api/v1/leads/${leadId}` : null,
  );

  const open = Boolean(leadId);
  const name = data
    ? `${data.lead.first_name ?? ""} ${data.lead.last_name ?? ""}`.trim() || "Lead"
    : "Lead";

  async function approve() {
    if (!leadId) return;
    try {
      await apiFetch(`/api/v1/leads/${leadId}/approve`, { method: "POST" });
      toast.push({ title: "Lead approved", tone: "success" });
      onMutated();
      mutate();
    } catch (e) {
      toast.push({
        title: "Approve failed",
        description: e instanceof Error ? e.message : String(e),
        tone: "error",
      });
    }
  }
  async function reject() {
    if (!leadId) return;
    try {
      await apiFetch(`/api/v1/leads/${leadId}/reject`, {
        method: "POST",
        body: { reason: "manual_reject" },
      });
      toast.push({ title: "Lead rejected" });
      onMutated();
      onClose();
    } catch (e) {
      toast.push({
        title: "Reject failed",
        description: e instanceof Error ? e.message : String(e),
        tone: "error",
      });
    }
  }
  async function rescore() {
    if (!leadId) return;
    try {
      await apiFetch(`/api/v1/leads/${leadId}/score`, { method: "POST" });
      toast.push({ title: "Re-scored", tone: "success" });
      mutate();
      onMutated();
    } catch (e) {
      toast.push({
        title: "Re-score failed",
        description: e instanceof Error ? e.message : String(e),
        tone: "error",
      });
    }
  }
  async function enrich() {
    if (!leadId) return;
    setEnriching(true);
    try {
      await apiFetch(`/api/v1/leads/${leadId}/enrich`, { method: "POST" });
      toast.push({
        title: "Research complete",
        description: "The brief below also feeds this lead's email drafts.",
        tone: "success",
      });
      mutate();
    } catch (e) {
      toast.push({
        title: "Research failed",
        description: e instanceof Error ? e.message : String(e),
        tone: "error",
      });
    } finally {
      setEnriching(false);
    }
  }

  return (
    <Drawer
      open={open}
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
      title={name}
      description={
        data
          ? `${data.lead.title ?? "—"}${data.account?.name ? ` · ${data.account.name}` : ""}`
          : undefined
      }
    >
      {error ? (
        <ErrorCard message={error?.message} onRetry={() => mutate()} />
      ) : isLoading || !data ? (
        <div className="space-y-3">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
      ) : (
        <div className="space-y-6">
          {/* state + actions */}
          <div className="flex items-center justify-between gap-3 -mt-1">
            <div className="flex items-center gap-2">
              <TierBadge tier={data.state?.tier} />
              <StateBadge state={data.state?.state} />
              {data.state?.score !== null && data.state?.score !== undefined ? (
                <span className="text-xs text-muted text-mono">
                  score {data.state.score.toFixed(1)}
                </span>
              ) : null}
            </div>
            <div className="flex items-center gap-1">
              {data.state?.state === "scored" ? (
                <Button size="sm" variant="primary" onClick={approve}>
                  <Check size={12} />
                  Approve
                </Button>
              ) : null}
              <ConfirmPopover
                trigger={
                  <Button size="sm" variant="danger">
                    <X size={12} />
                    Reject
                  </Button>
                }
                title="Reject this lead?"
                destructive
                onConfirm={reject}
              />
              <Button size="sm" variant="ghost" onClick={rescore}>
                <RefreshCcw size={12} />
              </Button>
            </div>
          </div>

          {/* Email — verify / edit */}
          <EmailRow
            leadId={leadId!}
            email={data.lead.email}
            confidence={data.lead.email_confidence}
            onChanged={() => {
              mutate();
              onMutated();
            }}
          />

          {/* AI research brief */}
          <Section title="Why this lead">
            {data.lead.enrichment ? (
              <div className="space-y-3">
                {data.lead.enrichment.website || data.account?.website ? (
                  <a
                    href={data.lead.enrichment.website ?? data.account?.website ?? "#"}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1.5 text-sm text-terracotta font-medium hover:underline"
                  >
                    <Globe size={13} />
                    {(data.lead.enrichment.website ?? data.account?.website ?? "")
                      .replace(/^https?:\/\//, "")
                      .replace(/\/$/, "")}
                    <ExternalLink size={11} />
                  </a>
                ) : null}

                {data.lead.enrichment.company_summary ? (
                  <p className="text-sm text-ink/85 leading-relaxed">
                    {data.lead.enrichment.company_summary}
                  </p>
                ) : null}

                {data.lead.enrichment.why_fit.length > 0 ? (
                  <div>
                    <div className="text-[11px] uppercase tracking-wider text-muted font-semibold mb-1.5">
                      Why they fit you
                    </div>
                    <ul className="space-y-1.5">
                      {data.lead.enrichment.why_fit.map((w, i) => (
                        <li key={i} className="flex items-start gap-2 text-sm text-ink/85 leading-relaxed">
                          <Check size={13} className="text-green mt-0.5 shrink-0" />
                          {w}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                {data.lead.enrichment.why_now ? (
                  <div>
                    <div className="text-[11px] uppercase tracking-wider text-muted font-semibold mb-1">
                      Why now
                    </div>
                    <p className="text-sm text-ink/85 leading-relaxed">
                      {data.lead.enrichment.why_now}
                    </p>
                  </div>
                ) : null}

                {data.lead.enrichment.hook ? (
                  <div className="bg-terracotta-wash/50 rounded-lg px-3 py-2.5">
                    <div className="text-[11px] uppercase tracking-wider text-terracotta font-semibold mb-1">
                      Opening hook
                    </div>
                    <p className="text-sm text-ink/90 italic leading-relaxed">
                      “{data.lead.enrichment.hook}”
                    </p>
                  </div>
                ) : null}

                <div className="flex items-center justify-between pt-1">
                  <span className="text-[11px] text-muted">
                    Researched {relativeTime(data.lead.enrichment.enriched_at)} ·
                    feeds this lead&apos;s email drafts
                  </span>
                  <Button size="sm" variant="ghost" onClick={enrich} disabled={enriching}>
                    <RefreshCcw size={12} />
                    {enriching ? "Researching…" : "Refresh"}
                  </Button>
                </div>
              </div>
            ) : (
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm text-muted">
                  Find their website and get a brief on why they fit your
                  profile — and how to open.
                </p>
                <Button size="sm" variant="primary" onClick={enrich} disabled={enriching}>
                  <Sparkles size={13} />
                  {enriching ? "Researching…" : "Research"}
                </Button>
              </div>
            )}
          </Section>

          {/* account */}
          {data.account ? (
            <Section title="Account">
              <KV label="Company" value={data.account.name} />
              <KV label="Country" value={data.account.country} />
              <KV label="Industry" value={data.account.industry} />
              <KV label="Stage" value={data.account.funding_stage} />
              <KV
                label="Employees"
                value={
                  data.account.employee_count !== null
                    ? String(data.account.employee_count)
                    : null
                }
              />
            </Section>
          ) : null}

          {/* lead */}
          <Section title="Person">
            <KV label="Title" value={data.lead.title} />
            <KV
              label="LinkedIn"
              value={
                data.lead.linkedin_url ? (
                  <a
                    href={data.lead.linkedin_url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-terracotta hover:underline break-all"
                  >
                    {data.lead.linkedin_url}
                  </a>
                ) : null
              }
            />
            <KV label="Source" value={data.lead.source} />
            <KV label="Notes" value={data.lead.notes} />
          </Section>

          {/* state details */}
          {data.state ? (
            <Section title="Sequence">
              <KV
                label="Started"
                value={
                  data.state.sequence_started_at
                    ? formatDateTime(data.state.sequence_started_at)
                    : null
                }
              />
              <KV
                label="Paused until"
                value={
                  data.state.sequence_paused_until
                    ? formatDateTime(data.state.sequence_paused_until)
                    : null
                }
              />
            </Section>
          ) : null}

          {/* signals */}
          <Section title="Signals" empty={data.signals.length === 0}>
            {data.signals.length === 0 ? (
              <span className="text-muted text-sm">None yet.</span>
            ) : (
              <ul className="space-y-2">
                {data.signals.map((s, i) => (
                  <li key={i} className="text-xs">
                    <span className="font-medium text-ink">{s.type}</span>
                    <span className="text-muted"> · {s.source} · </span>
                    <span className="text-muted">{relativeTime(s.observed_at)}</span>
                  </li>
                ))}
              </ul>
            )}
          </Section>

          {/* touches */}
          <Section title="Touches" empty={data.touches.length === 0}>
            {data.touches.length === 0 ? (
              <span className="text-muted text-sm">No touches yet.</span>
            ) : (
              <ul className="space-y-3">
                {data.touches.map((t) => (
                  <li
                    key={t.id}
                    className="border border-border rounded-sm p-3 bg-cream/40"
                  >
                    <div className="flex items-center gap-2 mb-1.5 text-[11px] text-muted">
                      <ChannelBadge channel={t.channel} />
                      <span>step {t.step_number ?? "—"}</span>
                      <span>· {t.status}</span>
                      <span className="ml-auto">
                        {t.sent_at ? `sent ${relativeTime(t.sent_at)}` : ""}
                        {t.drafted_at && !t.sent_at
                          ? `drafted ${relativeTime(t.drafted_at)}`
                          : ""}
                      </span>
                    </div>
                    {t.subject ? (
                      <div className="text-sm font-medium text-ink mb-1">
                        {t.subject}
                      </div>
                    ) : null}
                    {t.body ? (
                      <div className="text-xs text-ink/80 whitespace-pre-wrap">
                        {truncate(t.body, 600)}
                      </div>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </Section>
        </div>
      )}
    </Drawer>
  );
}

function Section({
  title,
  children,
  empty,
}: {
  title: string;
  children: React.ReactNode;
  empty?: boolean;
}) {
  return (
    <section>
      <div className="text-[11px] uppercase tracking-wider text-muted font-medium mb-2">
        {title}
      </div>
      <div className={empty ? "" : "space-y-1.5"}>{children}</div>
    </section>
  );
}

function KV({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <div className="flex items-start gap-3 text-sm">
      <span className="w-20 shrink-0 text-xs text-muted">{label}</span>
      <span className="text-ink flex-1 break-words">{value}</span>
    </div>
  );
}

function EmailRow({
  leadId,
  email,
  confidence,
  onChanged,
}: {
  leadId: string;
  email: string | null;
  confidence: string | null;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(email ?? "");
  const [busy, setBusy] = useState(false);

  const badge =
    confidence === "verified"
      ? { cls: "bg-green-wash text-green", label: "Verified" }
      : confidence === "risky"
        ? { cls: "bg-warning-wash text-warning", label: "Unverified" }
        : confidence === "guessed"
          ? { cls: "bg-warning-wash text-warning", label: "Guessed — won't send" }
          : { cls: "bg-cream text-muted", label: "Provided" };

  async function verify() {
    setBusy(true);
    try {
      const r = await apiFetch<{ ok: boolean; confidence?: string; reason?: string }>(
        `/api/v1/leads/${leadId}/verify-email`,
        { method: "POST" },
      );
      if (r.ok && r.confidence === "verified") {
        toast.push({ title: "Email verified", tone: "success" });
      } else {
        toast.push({
          title: "Couldn't verify",
          description:
            r.reason === "unverifiable_no_quota"
              ? "Verifier quota exhausted — enter the email manually to confirm."
              : "Mailbox not confirmed. Enter the correct email manually.",
          tone: "neutral",
        });
      }
      onChanged();
    } catch (e) {
      toast.push({ title: "Verify failed", description: e instanceof Error ? e.message : String(e), tone: "error" });
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    setBusy(true);
    try {
      await apiFetch(`/api/v1/leads/${leadId}/email`, {
        method: "POST",
        body: { email: value },
      });
      toast.push({ title: "Email updated · marked verified", tone: "success" });
      setEditing(false);
      onChanged();
    } catch (e) {
      toast.push({ title: "Update failed", description: e instanceof Error ? e.message : String(e), tone: "error" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section title="Email">
      {editing ? (
        <div className="flex items-center gap-2">
          <input
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="name@company.com"
            className="flex-1 px-3 py-1.5 bg-cream border border-border rounded-md text-sm focus:outline-none focus:border-terracotta"
          />
          <Button size="sm" variant="primary" disabled={busy || !value} onClick={save}>
            Save
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
            Cancel
          </Button>
        </div>
      ) : (
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="text-sm text-ink truncate">{email || "(none)"}</div>
            <span className={`inline-block mt-1 px-1.5 py-0.5 rounded-sm text-[11px] font-medium ${badge.cls}`}>
              {badge.label}
            </span>
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            {confidence !== "verified" ? (
              <Button size="sm" variant="secondary" disabled={busy} onClick={verify}>
                {busy ? "…" : "Verify"}
              </Button>
            ) : null}
            <Button size="sm" variant="ghost" onClick={() => { setValue(email ?? ""); setEditing(true); }}>
              Edit
            </Button>
          </div>
        </div>
      )}
      {confidence !== "verified" ? (
        <p className="text-[11px] text-muted mt-2">
          Only verified emails are sent — protects your sender reputation. Verify
          or enter the correct address to enable sending.
        </p>
      ) : null}
    </Section>
  );
}
