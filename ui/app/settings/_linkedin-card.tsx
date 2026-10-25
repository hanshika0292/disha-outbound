"use client";

import { useState } from "react";
import useSWR from "swr";
import { Handshake, CheckCircle2, ExternalLink, AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { apiFetch } from "@/lib/api";

type Status = {
  available: boolean;
  connected: boolean;
  auto: boolean;
  daily_cap: number;
  sent_today: number;
};

export function LinkedInCard() {
  const { data, mutate } = useSWR<Status>("/api/v1/linkedin/status");
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  async function connect() {
    setBusy(true);
    try {
      const res = await apiFetch<{ url: string }>("/api/v1/linkedin/connect", {
        method: "POST",
      });
      window.open(res.url, "_blank", "noopener");
      toast.push({
        title: "Opening LinkedIn connect…",
        description: "Finish in the new tab, then come back and refresh.",
        tone: "success",
      });
    } catch (e) {
      toast.push({ title: "Couldn't start connect", description: e instanceof Error ? e.message : String(e), tone: "error" });
    } finally {
      setBusy(false);
    }
  }

  async function patch(key: string, value: string | boolean) {
    try {
      await apiFetch("/api/v1/settings", { method: "PATCH", body: { [key]: value } });
      mutate();
    } catch (e) {
      toast.push({ title: "Save failed", description: e instanceof Error ? e.message : String(e), tone: "error" });
    }
  }

  return (
    <section className="bg-paper border border-border rounded-sm p-5">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="text-display text-xl mb-1 flex items-center gap-2">
            <Handshake size={17} className="text-terracotta" />
            LinkedIn outreach
          </div>
          <p className="text-xs text-muted">
            Connect LinkedIn to send connection invites with your AI-written note
            automatically. Capped daily to protect your account.
          </p>
        </div>
        {data?.connected && (
          <span className="inline-flex items-center gap-1.5 text-green text-sm font-medium shrink-0">
            <CheckCircle2 size={14} /> Connected
          </span>
        )}
      </div>

      {!data ? (
        <div className="mt-4 h-10 bg-cream rounded-sm animate-pulse" />
      ) : !data.available ? (
        <div className="mt-4 flex items-start gap-2 text-sm text-muted bg-cream border border-border rounded-sm p-3">
          <AlertTriangle size={15} className="text-warning mt-0.5 shrink-0" />
          <span>
            LinkedIn automation isn’t enabled on this workspace yet. (Unipile is
            not configured.) The manual LinkedIn queue still works.
          </span>
        </div>
      ) : !data.connected ? (
        <div className="mt-4">
          <Button variant="primary" onClick={connect} disabled={busy}>
            <ExternalLink size={14} />
            {busy ? "Starting…" : "Connect LinkedIn"}
          </Button>
          <p className="text-[11px] text-muted mt-2">
            Opens Unipile’s secure connect flow in a new tab. Use an established
            LinkedIn account — brand-new ones can’t send invites.
          </p>
        </div>
      ) : (
        <div className="mt-4 space-y-4">
          {/* Auto toggle */}
          <label className="flex items-center justify-between gap-4 cursor-pointer">
            <span className="text-sm">
              <span className="text-ink font-medium">Auto-send invites</span>
              <span className="block text-xs text-muted">
                Send queued LinkedIn invites automatically, up to the daily cap.
              </span>
            </span>
            <button
              type="button"
              role="switch"
              aria-checked={data.auto}
              onClick={() => patch("linkedin_auto", !data.auto)}
              className={`relative w-10 h-6 rounded-full transition-colors shrink-0 ${data.auto ? "bg-terracotta" : "bg-border-strong"}`}
            >
              <span className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform ${data.auto ? "translate-x-4" : ""}`} />
            </button>
          </label>

          {/* Daily cap + usage */}
          <div className="flex items-center justify-between gap-4">
            <span className="text-sm">
              <span className="text-ink font-medium">Daily invite cap</span>
              <span className="block text-xs text-muted">
                {data.sent_today} sent today
              </span>
            </span>
            <input
              type="number"
              min={1}
              max={50}
              defaultValue={data.daily_cap}
              onBlur={(e) => patch("linkedin_daily_cap", e.target.value)}
              className="w-20 px-3 py-1.5 bg-cream border border-border rounded-sm text-sm text-right focus:outline-none focus:border-terracotta"
            />
          </div>

          {data.auto && (
            <div className="flex items-start gap-2 text-xs text-muted bg-cream border border-border rounded-sm p-2.5">
              <AlertTriangle size={13} className="text-warning mt-0.5 shrink-0" />
              Keep volume low (~20/day) and personalised — LinkedIn restricts
              accounts that send too many invites.
            </div>
          )}
        </div>
      )}
    </section>
  );
}
