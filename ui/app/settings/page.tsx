"use client";

import useSWR from "swr";
import { useRef, useState } from "react";
import { PageHeader } from "../_components/header";
import { GmailConnectCard } from "./_gmail-card";
import { IcpWizardCard } from "./_icp-card";
import { LinkedInCard } from "./_linkedin-card";
import { Input, Label } from "@/components/ui/inputs";
import { StatusDot } from "@/components/ui/badges";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorCard } from "@/components/ui/error-card";
import { useToast } from "@/components/ui/toast";
import { apiFetch } from "@/lib/api";

type SettingsBody = {
  tenant: {
    id: string;
    slug: string | null;
    name: string | null;
    plan: string | null;
    status: string | null;
  };
  config: Record<string, string | boolean | null>;
  updated_at: string | null;
  byok: Record<string, { configured: boolean; active?: boolean; updated_at?: string }>;
  supported_keys: string[];
};

type Health = {
  overall: "pass" | "warn" | "fail";
  checks: Array<{ name: string; status: string; detail?: string }>;
};

const EDITABLE_TEXT = [
  { key: "sender_name", label: "Your name", placeholder: "Jane Doe" },
  { key: "sender_title", label: "Your title", placeholder: "AI Consultant" },
  { key: "sender_credentials", label: "Credentials (signature)", placeholder: "ex-Acme Corp · 10 yrs in product" },
  { key: "linkedin_url", label: "LinkedIn URL (signature)", placeholder: "linkedin.com/in/you" },
  { key: "sender_city", label: "Your city", placeholder: "Bengaluru" },
  { key: "cal_link", label: "Cal.com link", placeholder: "https://cal.com/…" },
  { key: "report_email", label: "Weekly report email", placeholder: "you@example.com" },
  { key: "notion_database_id", label: "Notion database ID", placeholder: "32-char hex" },
] as const;

export default function SettingsPage() {
  const { data, error, mutate, isLoading } =
    useSWR<SettingsBody>("/api/v1/settings");
  const { data: health } = useSWR<Health>("/api/v1/settings/health", {
    refreshInterval: 60_000,
  });
  const toast = useToast();

  // Track only KEYS the user has touched. Render uses serverValue(key) ?? editValue(key)
  // so we never need to "sync" server data into local state via useEffect.
  const [edits, setEdits] = useState<Record<string, string | boolean>>({});
  const [savingKey, setSavingKey] = useState<string | null>(null);

  function valueFor(key: string): string | boolean {
    if (key in edits) return edits[key];
    const v = data?.config[key];
    if (v === undefined || v === null) return "";
    if (typeof v === "boolean") return v;
    return String(v);
  }

  // Debounced autosave per key
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  function scheduleSave(key: string, value: string | boolean) {
    if (timers.current[key]) clearTimeout(timers.current[key]);
    timers.current[key] = setTimeout(() => save(key, value), 600);
  }
  async function save(key: string, value: string | boolean) {
    setSavingKey(key);
    try {
      await apiFetch("/api/v1/settings", {
        method: "PATCH",
        body: { [key]: value === "" ? null : value },
      });
      toast.push({ title: `Saved ${key}`, tone: "success" });
      // Drop the edit overlay once the server confirms; from now on the
      // server value is the source of truth (until next user edit).
      setEdits((cur) => {
        const { [key]: _omit, ...rest } = cur;
        return rest;
      });
      mutate();
    } catch (e) {
      toast.push({
        title: `Save failed`,
        description: e instanceof Error ? e.message : String(e),
        tone: "error",
      });
    } finally {
      setSavingKey(null);
    }
  }

  function setText(key: string, v: string) {
    setEdits((cur) => ({ ...cur, [key]: v }));
    scheduleSave(key, v);
  }
  function setBool(key: string, v: boolean) {
    setEdits((cur) => ({ ...cur, [key]: v }));
    save(key, v);
  }

  return (
    <>
      <PageHeader
        title="Settings"
        description="Sender profile, approval flow, and provider connections."
      />

      <div className="px-8 py-6 max-w-3xl space-y-8">
        {error ? (
          <ErrorCard message={error?.message} onRetry={() => mutate()} />
        ) : isLoading || !data ? (
          <Skeleton className="h-64 w-full" />
        ) : (
          <>
            {/* Sender profile */}
            <section className="bg-paper border border-border rounded-sm p-5">
              <div className="text-display text-xl mb-1">Sender profile</div>
              <p className="text-xs text-muted mb-4">
                Used in every draft. Autosaves after you stop typing.
              </p>
              <div className="grid grid-cols-2 gap-4">
                {EDITABLE_TEXT.map((f) => (
                  <div
                    key={f.key}
                    className={f.key === "cal_link" || f.key === "report_email" || f.key === "notion_database_id" ? "col-span-2" : ""}
                  >
                    <Label>
                      {f.label}
                      {savingKey === f.key ? (
                        <span className="ml-1.5 text-terracotta text-[10px]">saving…</span>
                      ) : null}
                    </Label>
                    <Input
                      value={String(valueFor(f.key) ?? "")}
                      onChange={(e) => setText(f.key, e.target.value)}
                      placeholder={f.placeholder}
                    />
                  </div>
                ))}
              </div>
            </section>

            {/* Ideal client profile */}
            <IcpWizardCard />

            {/* LinkedIn outreach (Unipile) */}
            <LinkedInCard />

            {/* Gmail connection */}
            <GmailConnectCard />

            {/* Automation / approval */}
            <section className="bg-paper border border-border rounded-sm p-5">
              <div className="text-display text-xl mb-1">Automation</div>
              <p className="text-xs text-muted mb-4">
                Choose how hands-off Disha runs. Sending guardrails (verified
                emails only, daily ramp, send window) always apply — even on
                full autopilot.
              </p>

              <div className="space-y-4">
                {/* Auto-approve leads */}
                <div className="border-b border-border pb-4">
                  <label className="flex items-center gap-3 text-sm">
                    <input
                      type="checkbox"
                      className="accent-terracotta scale-125"
                      checked={Boolean(valueFor("auto_approve_leads"))}
                      onChange={(e) => setBool("auto_approve_leads", e.target.checked)}
                    />
                    <span className="font-medium">Auto-approve leads</span>
                    {savingKey === "auto_approve_leads" ? (
                      <span className="text-terracotta text-[10px]">saving…</span>
                    ) : null}
                  </label>
                  <p className="text-xs text-muted mt-1 ml-7">
                    Newly scored leads at or above your bar enter the sequence
                    automatically — no review needed.
                  </p>
                  {Boolean(valueFor("auto_approve_leads")) ? (
                    <div className="ml-7 mt-2 flex items-center gap-2 text-sm">
                      <span className="text-muted text-xs">Approve tier:</span>
                      {(["A", "B", "C"] as const).map((t) => {
                        const cur = String(valueFor("auto_approve_tier") || "B");
                        return (
                          <button
                            key={t}
                            onClick={() => setText("auto_approve_tier", t)}
                            className={`px-2.5 py-1 rounded-md border text-xs font-medium ${
                              cur === t
                                ? "bg-terracotta text-white border-terracotta"
                                : "border-border-strong text-muted hover:text-ink"
                            }`}
                          >
                            {t}+
                          </button>
                        );
                      })}
                    </div>
                  ) : null}
                </div>

                {/* Auto-approve outreach */}
                <div>
                  <label className="flex items-center gap-3 text-sm">
                    <input
                      type="checkbox"
                      className="accent-terracotta scale-125"
                      checked={Boolean(valueFor("auto_approve_outreach"))}
                      onChange={(e) =>
                        setBool("auto_approve_outreach", e.target.checked)
                      }
                    />
                    <span className="font-medium">Auto-approve outreach</span>
                    {savingKey === "auto_approve_outreach" ? (
                      <span className="text-terracotta text-[10px]">saving…</span>
                    ) : null}
                  </label>
                  <p className="text-xs text-muted mt-1 ml-7">
                    Drafted emails send automatically without waiting in your
                    Drafts queue. (LinkedIn always stays manual — it can&apos;t
                    be sent by API.)
                  </p>
                </div>

                {Boolean(valueFor("auto_approve_leads")) &&
                Boolean(valueFor("auto_approve_outreach")) ? (
                  <div className="bg-warning-wash/60 border border-warning/30 rounded-md px-3 py-2 text-xs text-warning">
                    Full autopilot is on — leads are approved and emails send
                    with no manual step. Only verified emails go out, capped per
                    day. Use Pause in the sidebar to stop anytime.
                  </div>
                ) : null}
              </div>
            </section>

            {/* BYOK provider keys */}
            <section className="bg-paper border border-border rounded-sm p-5">
              <div className="text-display text-xl mb-1">Your AI keys (BYOK)</div>
              <p className="text-xs text-muted mb-4">
                Drafting, scoring, and research run on your own LLM key — you
                pay your provider directly, your data never rides a shared
                account. Keys are encrypted at rest and validated on save.
              </p>
              <div className="space-y-2">
                {Object.entries(data.byok).map(([provider, status]) => (
                  <ByokRow
                    key={provider}
                    provider={provider}
                    configured={status.configured}
                    updatedAt={status.updated_at}
                    onSaved={() => mutate()}
                  />
                ))}
              </div>
            </section>

            {/* Health */}
            <section className="bg-paper border border-border rounded-sm p-5">
              <div className="flex items-center justify-between mb-3">
                <div className="text-display text-xl">System health</div>
                <StatusDot
                  ok={health ? health.overall === "pass" : null}
                  label={health?.overall ?? "checking…"}
                />
              </div>
              {health ? (
                <ul className="text-sm divide-y divide-border">
                  {health.checks.map((c) => (
                    <li
                      key={c.name}
                      className="flex items-center justify-between py-2"
                    >
                      <span className="text-ink/85">{c.name}</span>
                      <span className="flex items-center gap-2 text-xs">
                        <span className="text-muted">{c.detail ?? ""}</span>
                        <StatusDot
                          ok={c.status === "pass"}
                          label={c.status}
                        />
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <Skeleton className="h-32 w-full" />
              )}
            </section>

            {/* Tenant card */}
            <section className="text-xs text-muted">
              <div>Tenant: {data.tenant.slug} ({data.tenant.id.slice(0, 8)}…)</div>
              <div>Plan: {data.tenant.plan} · Status: {data.tenant.status}</div>
              {data.updated_at ? (
                <div>Settings updated {new Date(data.updated_at).toLocaleString()}</div>
              ) : null}
            </section>
          </>
        )}
      </div>
    </>
  );
}

function ByokRow({
  provider,
  configured,
  updatedAt,
  onSaved,
}: {
  provider: string;
  configured: boolean;
  updatedAt?: string;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [key, setKey] = useState("");
  const [saving, setSaving] = useState(false);

  async function save() {
    setSaving(true);
    try {
      await apiFetch("/api/v1/settings/byok", {
        method: "POST",
        body: { provider, api_key: key },
      });
      toast.push({ title: `${provider} key saved & validated`, tone: "success" });
      setEditing(false);
      setKey("");
      onSaved();
    } catch (e) {
      toast.push({
        title: `${provider} key rejected`,
        description: e instanceof Error ? e.message : String(e),
        tone: "error",
      });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="border-b border-border last:border-0 py-2">
      <div className="flex items-center justify-between gap-3">
        <div className="text-sm">
          <span className="font-medium capitalize">{provider}</span>
          {updatedAt ? (
            <span className="text-xs text-muted ml-2">
              updated {new Date(updatedAt).toLocaleDateString()}
            </span>
          ) : null}
        </div>
        <div className="flex items-center gap-3">
          <StatusDot ok={configured} label={configured ? "Configured" : "Not set"} />
          <button
            className="text-xs text-terracotta font-medium hover:underline"
            onClick={() => setEditing((s) => !s)}
          >
            {editing ? "Cancel" : configured ? "Replace" : "Add key"}
          </button>
        </div>
      </div>
      {editing ? (
        <div className="flex items-center gap-2 mt-2">
          <Input
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder={`${provider} API key`}
            type="password"
          />
          <button
            className="shrink-0 px-3 py-2 bg-terracotta text-white text-xs font-semibold rounded-lg shadow-accent hover:brightness-110 disabled:opacity-50"
            disabled={!key || saving}
            onClick={save}
          >
            {saving ? "Validating…" : "Save"}
          </button>
        </div>
      ) : null}
    </div>
  );
}
