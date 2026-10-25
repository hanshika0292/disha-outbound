"use client";

import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import { useRouter, useSearchParams } from "next/navigation";
import { Mail, CheckCircle2, AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { apiFetch } from "@/lib/api";

type GmailStatus = {
  connected: boolean;
  sender_email: string | null;
  oauth_configured: boolean;
};

/** "Connect Gmail" card: shows status, kicks off the OAuth flow, and toasts
 * the ?gmail=connected|error result when Google bounces back here. */
export function GmailConnectCard() {
  const { data, mutate } = useSWR<GmailStatus>("/api/v1/gmail/status");
  const [connecting, setConnecting] = useState(false);
  const toast = useToast();
  const router = useRouter();
  const params = useSearchParams();
  const handledRef = useRef(false);

  // Handle the OAuth redirect result exactly once.
  useEffect(() => {
    const result = params.get("gmail");
    if (!result || handledRef.current) return;
    handledRef.current = true;
    if (result === "connected") {
      toast.push({ title: "Gmail connected", tone: "success" });
      mutate();
    } else {
      toast.push({
        title: "Gmail connection failed",
        description: params.get("reason") ?? undefined,
        tone: "error",
      });
    }
    router.replace("/settings");
  }, [params, toast, mutate, router]);

  async function connect() {
    setConnecting(true);
    try {
      const res = await apiFetch<{ auth_url?: string; error?: string }>(
        "/api/v1/gmail/connect",
        { method: "POST" },
      );
      if (res.auth_url) {
        window.location.href = res.auth_url;
      } else {
        toast.push({
          title: "Can't start Gmail connect",
          description: res.error,
          tone: "error",
        });
        setConnecting(false);
      }
    } catch (e) {
      toast.push({
        title: "Can't start Gmail connect",
        description: e instanceof Error ? e.message : String(e),
        tone: "error",
      });
      setConnecting(false);
    }
  }

  return (
    <section className="bg-paper border border-border rounded-sm p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="text-display text-xl mb-1 flex items-center gap-2">
            <Mail size={17} className="text-terracotta" />
            Gmail
          </div>
          <p className="text-xs text-muted">
            Connect your Gmail to send approved outreach and capture replies
            automatically.
          </p>
          {data?.connected && data.sender_email ? (
            <p className="inline-flex items-center gap-1.5 text-sm text-green font-medium mt-3">
              <CheckCircle2 size={14} />
              Connected as {data.sender_email}
            </p>
          ) : data && !data.oauth_configured ? (
            <p className="inline-flex items-center gap-1.5 text-xs text-warning mt-3">
              <AlertCircle size={13} />
              Server is missing GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET — set them
              first.
            </p>
          ) : null}
        </div>
        <Button
          variant={data?.connected ? "secondary" : "primary"}
          onClick={connect}
          disabled={connecting || (data ? !data.oauth_configured : false)}
        >
          {connecting
            ? "Redirecting…"
            : data?.connected
              ? "Reconnect"
              : "Connect Gmail"}
        </Button>
      </div>
    </section>
  );
}
