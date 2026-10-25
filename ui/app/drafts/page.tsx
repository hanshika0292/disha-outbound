"use client";

import useSWR from "swr";
import { useState } from "react";
import { Check, X, RefreshCcw, PenLine } from "lucide-react";
import { PageHeader } from "../_components/header";
import { Button } from "@/components/ui/button";
import { Input, Label, Textarea } from "@/components/ui/inputs";
import { ChannelBadge } from "@/components/ui/badges";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorCard } from "@/components/ui/error-card";
import { EmptyState } from "@/components/ui/empty-state";
import { ConfirmPopover } from "@/components/ui/confirm-popover";
import { useToast } from "@/components/ui/toast";
import { apiFetch } from "@/lib/api";
import { formatDateTime, relativeTime } from "@/lib/format";
import { cn } from "@/lib/cn";

type Draft = {
  touch_id: string;
  lead_id: string;
  lead_name: string;
  lead_title: string;
  account_name: string;
  step_number: number | null;
  channel: string;
  subject: string | null;
  body: string;
  drafted_at: string | null;
};

type Edits = Record<string, { subject: string; body: string }>;

export default function DraftsPage() {
  const { data, error, mutate, isLoading } = useSWR<Draft[]>(
    "/api/v1/drafts/pending/full",
    { refreshInterval: 30_000 },
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [edits, setEdits] = useState<Edits>({});
  const [working, setWorking] = useState(false);
  const toast = useToast();

  // Derive the active draft during render: prefer user's pick, else first.
  // No useEffect → no setState-in-effect cascade.
  const selected =
    (selectedId ? data?.find((d) => d.touch_id === selectedId) : null) ??
    data?.[0] ??
    null;

  function getEdit(d: Draft): { subject: string; body: string } {
    return (
      edits[d.touch_id] ?? {
        subject: d.subject ?? "",
        body: d.body ?? "",
      }
    );
  }
  function setEdit(id: string, patch: Partial<{ subject: string; body: string }>) {
    setEdits((prev) => {
      const cur = prev[id] ?? {
        subject: selected?.subject ?? "",
        body: selected?.body ?? "",
      };
      return { ...prev, [id]: { ...cur, ...patch } };
    });
  }

  function dirty(d: Draft): boolean {
    const e = edits[d.touch_id];
    if (!e) return false;
    return e.subject !== (d.subject ?? "") || e.body !== (d.body ?? "");
  }

  async function approve(d: Draft) {
    setWorking(true);
    try {
      const body: { subject?: string; body?: string } = {};
      if (dirty(d)) {
        const e = edits[d.touch_id]!;
        body.subject = e.subject;
        body.body = e.body;
      }
      await apiFetch(`/api/v1/drafts/${d.touch_id}/approve`, {
        method: "POST",
        body: Object.keys(body).length ? body : undefined,
      });
      toast.push({ title: "Approved · queued for send", tone: "success" });
      setEdits((prev) => {
        const { [d.touch_id]: _omit, ...rest } = prev;
        return rest;
      });
      mutate();
    } catch (e) {
      toast.push({
        title: "Approve failed",
        description: e instanceof Error ? e.message : String(e),
        tone: "error",
      });
    } finally {
      setWorking(false);
    }
  }

  async function reject(d: Draft) {
    setWorking(true);
    try {
      await apiFetch(`/api/v1/drafts/${d.touch_id}/reject`, {
        method: "POST",
        body: { reason: "rejected via UI" },
      });
      toast.push({ title: "Draft rejected" });
      mutate();
    } catch (e) {
      toast.push({
        title: "Reject failed",
        description: e instanceof Error ? e.message : String(e),
        tone: "error",
      });
    } finally {
      setWorking(false);
    }
  }

  async function regenerate(d: Draft) {
    setWorking(true);
    try {
      const res = await apiFetch<{ new_touch_id: string | null }>(
        `/api/v1/drafts/${d.touch_id}/regenerate`,
        { method: "POST" },
      );
      toast.push({
        title: res.new_touch_id ? "Draft regenerated" : "Couldn't regenerate (no prompt)",
        tone: res.new_touch_id ? "success" : "neutral",
      });
      setEdits((prev) => {
        const { [d.touch_id]: _omit, ...rest } = prev;
        return rest;
      });
      mutate();
    } catch (e) {
      toast.push({
        title: "Regenerate failed",
        description: e instanceof Error ? e.message : String(e),
        tone: "error",
      });
    } finally {
      setWorking(false);
    }
  }

  const edit = selected ? getEdit(selected) : null;
  const selectedDirty = selected ? dirty(selected) : false;

  return (
    <>
      <PageHeader
        title="Drafts"
        description="Edit then approve. Every approval queues the touch for send."
        actions={
          <span className="text-xs text-muted">
            {data ? `${data.length} pending` : ""}
          </span>
        }
      />

      {error ? (
        <div className="px-8 py-5">
          <ErrorCard message={error?.message} onRetry={() => mutate()} />
        </div>
      ) : isLoading || !data ? (
        <div className="px-8 py-5">
          <Skeleton className="h-[60vh] w-full" />
        </div>
      ) : data.length === 0 ? (
        <div className="px-8 py-10">
          <EmptyState
            title="No drafts waiting"
            description="When a sequence step fires, the draft shows up here for your approval. Set Require draft approval in Settings to control this."
            icon={<PenLine size={24} />}
          />
        </div>
      ) : (
        <div className="grid grid-cols-[320px_1fr] gap-0 border-t border-border min-h-[calc(100vh-9rem)]">
          {/* left: list */}
          <ul className="border-r border-border overflow-y-auto scrollbar-thin">
            {data.map((d) => {
              const isSel = d.touch_id === selectedId;
              const isDirty = dirty(d);
              return (
                <li
                  key={d.touch_id}
                  onClick={() => setSelectedId(d.touch_id)}
                  className={cn(
                    "border-b border-border px-4 py-3 cursor-pointer",
                    isSel ? "bg-terracotta-wash" : "hover:bg-cream",
                  )}
                >
                  <div className="flex items-center justify-between gap-2 mb-1">
                    <span className="text-sm font-medium text-ink truncate">
                      {d.lead_name || "—"}
                    </span>
                    <ChannelBadge channel={d.channel} />
                  </div>
                  <div className="text-xs text-muted truncate">
                    {d.lead_title}
                    {d.account_name ? ` · ${d.account_name}` : ""}
                  </div>
                  <div className="flex items-center justify-between mt-1.5">
                    <span className="text-[10px] text-muted">
                      step {d.step_number ?? "—"} · {relativeTime(d.drafted_at)}
                    </span>
                    {isDirty ? (
                      <span className="text-[10px] text-terracotta font-medium">
                        edited
                      </span>
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ul>

          {/* right: editor */}
          {selected && edit ? (
            <div className="p-6 space-y-4 overflow-y-auto scrollbar-thin">
              <div className="flex items-start justify-between gap-3 pb-3 border-b border-border">
                <div>
                  <div className="text-display text-2xl text-ink">
                    {selected.lead_name}
                  </div>
                  <div className="text-sm text-muted mt-0.5">
                    {selected.lead_title}
                    {selected.account_name ? ` · ${selected.account_name}` : ""}
                    {selected.drafted_at
                      ? ` · drafted ${formatDateTime(selected.drafted_at)}`
                      : ""}
                  </div>
                </div>
                <div className="flex items-center gap-1.5 shrink-0">
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={working}
                    onClick={() => regenerate(selected)}
                  >
                    <RefreshCcw size={12} />
                    Regenerate
                  </Button>
                  <ConfirmPopover
                    trigger={
                      <Button variant="danger" size="sm" disabled={working}>
                        <X size={12} />
                        Reject
                      </Button>
                    }
                    title="Reject this draft?"
                    description="The touch will be marked skipped and the lead moves on."
                    destructive
                    onConfirm={() => reject(selected)}
                  />
                  <Button
                    variant="primary"
                    size="sm"
                    disabled={working}
                    onClick={() => approve(selected)}
                  >
                    <Check size={12} />
                    {selectedDirty ? "Save & approve" : "Approve"}
                  </Button>
                </div>
              </div>

              {selected.channel === "email" ? (
                <div>
                  <Label>Subject</Label>
                  <Input
                    value={edit.subject}
                    onChange={(e) =>
                      setEdit(selected.touch_id, { subject: e.target.value })
                    }
                  />
                </div>
              ) : null}

              <div>
                <Label>Body</Label>
                <Textarea
                  value={edit.body}
                  onChange={(e) =>
                    setEdit(selected.touch_id, { body: e.target.value })
                  }
                  className="min-h-[24rem] text-mono text-[13px] leading-relaxed"
                />
                <div className="text-[11px] text-muted mt-1.5 flex items-center justify-between">
                  <span>{edit.body.length} chars</span>
                  {selectedDirty ? (
                    <span className="text-terracotta">Unsaved edits</span>
                  ) : null}
                </div>
              </div>
            </div>
          ) : (
            <div className="p-8 text-muted text-sm">
              Select a draft from the list.
            </div>
          )}
        </div>
      )}
    </>
  );
}
