"use client";

import useSWR from "swr";
import { useState } from "react";
import Link from "next/link";
import {
  Inbox as InboxIcon,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  Sparkles,
  Send,
} from "lucide-react";
import { PageHeader } from "../_components/header";
import { Button } from "@/components/ui/button";
import { SkeletonRows } from "@/components/ui/skeleton";
import { ErrorCard } from "@/components/ui/error-card";
import { EmptyState } from "@/components/ui/empty-state";
import { useToast } from "@/components/ui/toast";
import { apiFetch } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import { cn } from "@/lib/cn";

type InboxItem = {
  reply_id: string;
  lead_id: string;
  lead_name: string;
  lead_title: string | null;
  company: string;
  lead_state: string | null;
  subject: string | null;
  body: string;
  classification: Record<string, unknown> | null;
  label: string | null;
  received_at: string;
  our_touch_subject: string | null;
  our_touch_step: number | null;
};

type InboxResponse = {
  items: InboxItem[];
  total: number;
  page: number;
  page_size: number;
};

const LABEL_STYLES: Record<string, string> = {
  interested: "bg-green-wash text-green",
  meeting_request: "bg-green-wash text-green",
  positive: "bg-green-wash text-green",
  objection: "bg-warning-wash text-warning",
  not_now: "bg-warning-wash text-warning",
  question: "bg-terracotta-wash text-terracotta",
  referral: "bg-terracotta-wash text-terracotta",
  unsubscribe: "bg-red-50 text-red-600",
  negative: "bg-red-50 text-red-600",
  unclassified: "bg-cream text-muted",
};

function labelStyle(label: string | null): string {
  return LABEL_STYLES[label ?? "unclassified"] ?? "bg-cream text-muted";
}

type Draft = { subject: string; body: string; loading: boolean; sending: boolean };

export default function InboxPage() {
  const { data, error, mutate, isLoading } = useSWR<InboxResponse>(
    "/api/v1/inbox?page=1&page_size=50",
    { refreshInterval: 60_000 },
  );
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const toast = useToast();

  function toggle(id: string) {
    setExpanded((cur) => {
      const next = new Set(cur);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function patchDraft(id: string, patch: Partial<Draft>) {
    setDrafts((cur) => {
      const base: Draft = cur[id] ?? {
        subject: "",
        body: "",
        loading: false,
        sending: false,
      };
      return { ...cur, [id]: { ...base, ...patch } };
    });
  }

  async function suggest(replyId: string) {
    patchDraft(replyId, { loading: true });
    try {
      const res = await apiFetch<{ subject: string; body: string }>(
        `/api/v1/inbox/${replyId}/suggest`,
        { method: "POST" },
      );
      patchDraft(replyId, { subject: res.subject, body: res.body, loading: false });
    } catch (e) {
      patchDraft(replyId, { loading: false });
      toast.push({
        title: "Couldn't draft a reply",
        description: e instanceof Error ? e.message : String(e),
        tone: "error",
      });
    }
  }

  async function sendDraft(replyId: string) {
    const d = drafts[replyId];
    if (!d?.body) return;
    patchDraft(replyId, { sending: true });
    try {
      const res = await apiFetch<{ sent: boolean; to: string }>(
        `/api/v1/inbox/${replyId}/send`,
        { method: "POST", body: { subject: d.subject, body: d.body } },
      );
      toast.push({ title: `Reply sent to ${res.to}`, tone: "success" });
      setDrafts((cur) => {
        const { [replyId]: _omit, ...rest } = cur;
        return rest;
      });
      mutate();
    } catch (e) {
      patchDraft(replyId, { sending: false });
      toast.push({
        title: "Send failed",
        description: e instanceof Error ? e.message : String(e),
        tone: "error",
      });
    }
  }

  return (
    <>
      <PageHeader
        title="Inbox"
        description="Replies from your outreach, classified by AI. Click a reply to read it in full."
      />

      <div className="px-8 py-6 page-enter">
        {error ? (
          <ErrorCard
            message={error?.message ?? "Couldn't load inbox"}
            onRetry={() => mutate()}
          />
        ) : isLoading || !data ? (
          <SkeletonRows rows={6} />
        ) : data.items.length === 0 ? (
          <EmptyState
            title="No replies yet"
            description="When prospects respond to your outreach, their replies land here with an AI classification and a suggested next step."
            icon={<InboxIcon size={22} />}
          />
        ) : (
          <div className="space-y-2.5 max-w-4xl">
            {data.items.map((item) => {
              const open = expanded.has(item.reply_id);
              const reasoning =
                (item.classification?.reasoning as string | undefined) ??
                (item.classification?.reason as string | undefined);
              return (
                <div
                  key={item.reply_id}
                  className="bg-paper border border-border rounded-xl shadow-card overflow-hidden"
                >
                  {/* Row header */}
                  <button
                    onClick={() => toggle(item.reply_id)}
                    className="w-full flex items-center gap-3 px-5 py-3.5 text-left hover:bg-cream/60 transition-colors"
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-semibold text-ink text-sm truncate">
                          {item.lead_name || "Unknown"}
                        </span>
                        {item.company ? (
                          <span className="text-muted text-xs truncate">
                            · {item.company}
                          </span>
                        ) : null}
                        {item.label && (
                          <span
                            className={cn(
                              "px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wide shrink-0",
                              labelStyle(item.label),
                            )}
                          >
                            {item.label.replace(/_/g, " ")}
                          </span>
                        )}
                      </div>
                      <div className="text-xs text-muted truncate mt-0.5">
                        {item.subject || "(no subject)"} —{" "}
                        {item.body.slice(0, 110)}
                      </div>
                    </div>
                    <span className="text-xs text-muted shrink-0">
                      {relativeTime(item.received_at)}
                    </span>
                    {open ? (
                      <ChevronUp size={15} className="text-muted shrink-0" />
                    ) : (
                      <ChevronDown size={15} className="text-muted shrink-0" />
                    )}
                  </button>

                  {/* Expanded body */}
                  {open && (
                    <div className="px-5 pb-4 border-t border-border">
                      <pre className="whitespace-pre-wrap text-sm text-ink/90 font-sans leading-relaxed pt-3.5 max-h-72 overflow-y-auto scrollbar-thin">
                        {item.body}
                      </pre>

                      {reasoning && (
                        <div className="mt-3 flex items-start gap-2 bg-terracotta-wash/50 rounded-lg px-3 py-2.5">
                          <Sparkles
                            size={13}
                            className="text-terracotta mt-0.5 shrink-0"
                          />
                          <p className="text-xs text-ink/80 leading-relaxed">
                            {reasoning}
                          </p>
                        </div>
                      )}

                      <div className="mt-3 flex items-center justify-between">
                        <span className="text-xs text-muted">
                          In reply to:{" "}
                          {item.our_touch_subject || "your outreach"}
                          {item.our_touch_step
                            ? ` (step ${item.our_touch_step})`
                            : ""}
                        </span>
                        <Link
                          href={`/leads?lead=${item.lead_id}`}
                          className="inline-flex items-center gap-1.5 text-xs font-medium text-terracotta hover:underline"
                        >
                          View lead
                          <ExternalLink size={12} />
                        </Link>
                      </div>

                      {/* ── Reply composer ── */}
                      <div className="mt-4 border-t border-border pt-4">
                        {drafts[item.reply_id]?.body !== undefined &&
                        drafts[item.reply_id]?.body !== "" ? (
                          <div className="space-y-2.5">
                            <input
                              value={drafts[item.reply_id].subject}
                              onChange={(e) =>
                                patchDraft(item.reply_id, { subject: e.target.value })
                              }
                              className="w-full px-3 py-2 bg-cream border border-border rounded-lg text-sm
                                focus:outline-none focus:border-terracotta"
                              placeholder="Subject"
                            />
                            <textarea
                              value={drafts[item.reply_id].body}
                              onChange={(e) =>
                                patchDraft(item.reply_id, { body: e.target.value })
                              }
                              rows={7}
                              className="w-full px-3 py-2.5 bg-cream border border-border rounded-lg text-sm
                                leading-relaxed resize-y focus:outline-none focus:border-terracotta"
                            />
                            <div className="flex items-center justify-end gap-2">
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => suggest(item.reply_id)}
                                disabled={drafts[item.reply_id]?.loading}
                              >
                                <Sparkles size={13} />
                                Redraft
                              </Button>
                              <Button
                                variant="primary"
                                size="sm"
                                onClick={() => sendDraft(item.reply_id)}
                                disabled={drafts[item.reply_id]?.sending}
                              >
                                <Send size={13} />
                                {drafts[item.reply_id]?.sending
                                  ? "Sending…"
                                  : "Send reply"}
                              </Button>
                            </div>
                          </div>
                        ) : (
                          <Button
                            variant="secondary"
                            size="sm"
                            onClick={() => suggest(item.reply_id)}
                            disabled={drafts[item.reply_id]?.loading}
                          >
                            <Sparkles size={13} />
                            {drafts[item.reply_id]?.loading
                              ? "Drafting…"
                              : "Suggest reply"}
                          </Button>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </>
  );
}
