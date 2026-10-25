"use client";

import useSWR from "swr";
import { useState } from "react";
import {
  Copy,
  Check,
  X,
  Handshake,
  ExternalLink,
  Sparkles,
  Search,
  Send,
} from "lucide-react";
import { PageHeader } from "../_components/header";
import { Button } from "@/components/ui/button";
import { SkeletonRows } from "@/components/ui/skeleton";
import { ErrorCard } from "@/components/ui/error-card";
import { EmptyState } from "@/components/ui/empty-state";
import { ConfirmPopover } from "@/components/ui/confirm-popover";
import { useToast } from "@/components/ui/toast";
import { Textarea, Label } from "@/components/ui/inputs";
import { apiFetch } from "@/lib/api";
import { relativeTime } from "@/lib/format";

type Action = {
  id: string;
  lead_id: string;
  lead_name: string;
  lead_title: string;
  company: string;
  linkedin_url: string | null;
  linkedin_search_url: string | null;
  action_type: string;
  invite_note: string;
  connected_note: string;
  has_messages: boolean;
  status: string;
  created_at: string;
  completed_at: string | null;
  notes: string | null;
};

const INVITE_LIMIT = 300;

export default function LinkedInPage() {
  const [status, setStatus] = useState<"pending" | "done" | "skipped">("pending");
  const { data, error, mutate, isLoading } = useSWR<Action[]>(
    `/api/v1/linkedin/queue?status=${status}`,
  );
  const { data: liStatus } = useSWR<{ connected: boolean }>(
    "/api/v1/linkedin/status",
  );
  const [notesById, setNotesById] = useState<Record<string, string>>({});
  const [working, setWorking] = useState<string | null>(null);
  const [generating, setGenerating] = useState<string | null>(null);
  const toast = useToast();

  async function copy(text: string, label: string) {
    try {
      await navigator.clipboard.writeText(text);
      toast.push({ title: `${label} copied`, tone: "success" });
    } catch {
      toast.push({ title: "Copy failed — select & copy manually", tone: "error" });
    }
  }

  async function generate(a: Action) {
    setGenerating(a.id);
    try {
      await apiFetch(`/api/v1/linkedin/${a.id}/generate`, { method: "POST" });
      toast.push({ title: "Messages generated", tone: "success" });
      mutate();
    } catch (e) {
      toast.push({
        title: "Generate failed",
        description: e instanceof Error ? e.message : String(e),
        tone: "error",
      });
    } finally {
      setGenerating(null);
    }
  }

  async function sendViaLinkedIn(a: Action) {
    setWorking(a.id);
    try {
      await apiFetch(`/api/v1/linkedin/${a.id}/send`, { method: "POST" });
      toast.push({ title: "Invite sent on LinkedIn", tone: "success" });
      mutate();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toast.push({
        title: "LinkedIn send failed",
        description: msg.includes("no_linkedin_url")
          ? "This lead has no LinkedIn profile URL."
          : msg,
        tone: "error",
      });
    } finally {
      setWorking(null);
    }
  }

  async function markDone(a: Action) {
    setWorking(a.id);
    try {
      await apiFetch(`/api/v1/linkedin/${a.id}/done`, {
        method: "POST",
        body: { notes: notesById[a.id] ?? null },
      });
      toast.push({ title: "Marked done · touch recorded", tone: "success" });
      mutate();
    } catch (e) {
      toast.push({
        title: "Failed",
        description: e instanceof Error ? e.message : String(e),
        tone: "error",
      });
    } finally {
      setWorking(null);
    }
  }

  async function skip(a: Action) {
    setWorking(a.id);
    try {
      await apiFetch(`/api/v1/linkedin/${a.id}/skip`, {
        method: "POST",
        body: { reason: "not_relevant" },
      });
      toast.push({ title: "Skipped" });
      mutate();
    } catch (e) {
      toast.push({
        title: "Failed",
        description: e instanceof Error ? e.message : String(e),
        tone: "error",
      });
    } finally {
      setWorking(null);
    }
  }

  return (
    <>
      <PageHeader
        title="LinkedIn"
        description="Open their profile, copy the note, send the connection request, mark done."
      />

      <div className="px-8 py-5 space-y-4">
        <div className="flex items-center gap-1 border-b border-border">
          {(["pending", "done", "skipped"] as const).map((s) => (
            <button
              key={s}
              onClick={() => setStatus(s)}
              className={`px-3 py-2 text-sm border-b-2 -mb-px transition-colors ${
                status === s
                  ? "border-terracotta text-terracotta font-medium"
                  : "border-transparent text-muted hover:text-ink"
              }`}
            >
              {s[0].toUpperCase() + s.slice(1)}
            </button>
          ))}
        </div>

        {error ? (
          <ErrorCard message={error?.message} onRetry={() => mutate()} />
        ) : isLoading || !data ? (
          <SkeletonRows rows={4} />
        ) : data.length === 0 ? (
          <EmptyState
            title="Nothing in the queue"
            description="The sequencer adds LinkedIn touches here when a step fires. Mark done after you've sent the message."
            icon={<Handshake size={24} />}
          />
        ) : (
          <div className="space-y-3 max-w-3xl">
            {data.map((a) => {
              const inviteLen = a.invite_note.length;
              const over = inviteLen > INVITE_LIMIT;
              return (
                <div
                  key={a.id}
                  className="bg-paper border border-border rounded-md p-4"
                >
                  {/* Header: name + profile link + actions */}
                  <div className="flex items-start justify-between gap-3 mb-3">
                    <div className="min-w-0">
                      <div className="text-sm font-medium text-ink">
                        {a.lead_name || "—"}
                      </div>
                      <div className="text-xs text-muted">
                        {a.lead_title}
                        {a.company ? ` · ${a.company}` : ""} · added{" "}
                        {relativeTime(a.created_at)}
                      </div>
                      {/* Profile link / search fallback */}
                      <div className="mt-1.5">
                        {a.linkedin_url ? (
                          <a
                            href={a.linkedin_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-1.5 text-xs font-medium text-[#0a66c2] hover:underline"
                          >
                            <ExternalLink size={12} />
                            Open LinkedIn profile
                          </a>
                        ) : a.linkedin_search_url ? (
                          <a
                            href={a.linkedin_search_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-1.5 text-xs font-medium text-muted hover:text-ink hover:underline"
                          >
                            <Search size={12} />
                            Find on LinkedIn
                            <ExternalLink size={11} />
                          </a>
                        ) : null}
                      </div>
                    </div>
                    {status === "pending" ? (
                      <div className="flex items-center gap-1.5 shrink-0">
                        {liStatus?.connected && a.linkedin_url && (
                          <Button
                            size="sm"
                            variant="primary"
                            disabled={working === a.id}
                            onClick={() => sendViaLinkedIn(a)}
                            title="Send the connection invite via your connected LinkedIn"
                          >
                            <Send size={12} />
                            Send
                          </Button>
                        )}
                        <Button
                          size="sm"
                          variant={liStatus?.connected && a.linkedin_url ? "secondary" : "primary"}
                          disabled={working === a.id}
                          onClick={() => markDone(a)}
                        >
                          <Check size={12} />
                          Done
                        </Button>
                        <ConfirmPopover
                          trigger={
                            <Button size="sm" variant="danger">
                              <X size={12} />
                              Skip
                            </Button>
                          }
                          title="Skip this?"
                          destructive
                          onConfirm={() => skip(a)}
                        />
                      </div>
                    ) : (
                      <span className="text-xs text-muted shrink-0">
                        {a.completed_at
                          ? `${a.status} · ${relativeTime(a.completed_at)}`
                          : a.status}
                      </span>
                    )}
                  </div>

                  {/* Messages */}
                  {a.has_messages ? (
                    <div className="space-y-3">
                      {/* Invite note */}
                      <div>
                        <div className="flex items-center justify-between mb-1">
                          <Label>Connection invite note</Label>
                          <div className="flex items-center gap-2">
                            <span
                              className={`text-[11px] tabular-nums ${over ? "text-red-600 font-semibold" : "text-muted"}`}
                            >
                              {inviteLen}/{INVITE_LIMIT}
                            </span>
                            <button
                              onClick={() => copy(a.invite_note, "Invite note")}
                              className="inline-flex items-center gap-1 text-xs text-terracotta font-medium hover:underline"
                            >
                              <Copy size={11} />
                              Copy
                            </button>
                          </div>
                        </div>
                        <pre className="bg-cream border border-border rounded-md p-3 text-[13px] leading-relaxed whitespace-pre-wrap text-ink/90 font-sans">
                          {a.invite_note || "(empty)"}
                        </pre>
                      </div>

                      {/* Connected note */}
                      {a.connected_note ? (
                        <div>
                          <div className="flex items-center justify-between mb-1">
                            <Label>Message after they accept</Label>
                            <button
                              onClick={() => copy(a.connected_note, "Follow-up")}
                              className="inline-flex items-center gap-1 text-xs text-terracotta font-medium hover:underline"
                            >
                              <Copy size={11} />
                              Copy
                            </button>
                          </div>
                          <pre className="bg-cream border border-border rounded-md p-3 text-[13px] leading-relaxed whitespace-pre-wrap text-ink/90 font-sans">
                            {a.connected_note}
                          </pre>
                        </div>
                      ) : null}

                      {status === "pending" ? (
                        <button
                          onClick={() => generate(a)}
                          disabled={generating === a.id}
                          className="inline-flex items-center gap-1.5 text-xs text-muted hover:text-ink"
                        >
                          <Sparkles size={12} />
                          {generating === a.id ? "Regenerating…" : "Regenerate"}
                        </button>
                      ) : null}
                    </div>
                  ) : (
                    <div className="flex items-center justify-between gap-3 bg-cream border border-dashed border-border rounded-md px-4 py-3">
                      <span className="text-sm text-muted">
                        No message yet — generate an invite note and a follow-up.
                      </span>
                      <Button
                        size="sm"
                        variant="primary"
                        disabled={generating === a.id}
                        onClick={() => generate(a)}
                      >
                        <Sparkles size={12} />
                        {generating === a.id ? "Generating…" : "Generate"}
                      </Button>
                    </div>
                  )}

                  {/* Notes */}
                  {status === "pending" ? (
                    <div className="mt-3">
                      <Label>Notes (optional)</Label>
                      <Textarea
                        value={notesById[a.id] ?? ""}
                        onChange={(e) =>
                          setNotesById((n) => ({ ...n, [a.id]: e.target.value }))
                        }
                        placeholder="What did they say? Any context worth remembering."
                        className="min-h-[3.5rem]"
                      />
                    </div>
                  ) : a.notes ? (
                    <div className="text-xs text-muted mt-2 italic">
                      Notes: {a.notes}
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </>
  );
}
