"use client";

import useSWR from "swr";
import { useState } from "react";
import {
  Users,
  Mail,
  MessageSquare,
  DollarSign,
  Plus,
  Power,
  ClipboardList,
} from "lucide-react";
import { PageHeader } from "../_components/header";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/inputs";
import { StatCard } from "@/components/ui/stat-card";
import { SkeletonRows } from "@/components/ui/skeleton";
import { ErrorCard } from "@/components/ui/error-card";
import { useToast } from "@/components/ui/toast";
import { apiFetch } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import { cn } from "@/lib/cn";

type TenantRow = {
  id: string;
  slug: string;
  name: string;
  plan: string;
  active: boolean;
  created_at: string | null;
  users: string[];
  gmail_connected: boolean;
  leads_total: number;
  sends_7d: number;
  replies_7d: number;
  llm_cost_7d_usd: number;
};

type Overview = { tenants: TenantRow[]; waitlist_count: number };

type WaitlistRow = {
  email: string;
  name: string | null;
  note: string | null;
  source: string | null;
  answers: Record<string, string>;
  invited_at: string | null;
  created_at: string | null;
};

type InviteResult = {
  slug: string;
  email: string;
  password: string;
  login_url: string;
  welcome_email_sent?: boolean;
};

const ANSWER_LABELS: Record<string, string> = {
  exploring: "exploring",
  starting: "starting out",
  few_clients: "a few clients in",
  established: "established",
  referrals: "via referrals",
  outbound: "does outbound",
  inbound: "inbound/content",
  marketplaces: "marketplaces",
  none_yet: "no channel yet",
};

export default function AdminPage() {
  const { data, error, mutate, isLoading } = useSWR<Overview>(
    "/api/v1/admin/overview",
    { refreshInterval: 60_000 },
  );
  const { data: waitlist, mutate: mutateWaitlist } = useSWR<WaitlistRow[]>(
    "/api/v1/admin/waitlist",
  );
  const toast = useToast();
  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({
    slug: "",
    name: "",
    email: "",
    password: "",
    sender_title: "",
  });

  const totals = data?.tenants.reduce(
    (acc, t) => ({
      leads: acc.leads + t.leads_total,
      sends: acc.sends + t.sends_7d,
      replies: acc.replies + t.replies_7d,
      cost: acc.cost + t.llm_cost_7d_usd,
    }),
    { leads: 0, sends: 0, replies: 0, cost: 0 },
  );

  async function createTenant() {
    setCreating(true);
    try {
      const res = await apiFetch<{
        slug: string;
        user_email: string;
        login_url: string;
        welcome_email_sent?: boolean;
      }>("/api/v1/admin/tenants", { method: "POST", body: form });
      toast.push({
        title: `Account "${res.slug}" created`,
        description: res.welcome_email_sent
          ? `Welcome email with login details sent to ${res.user_email}`
          : `⚠ Welcome email failed — manually send ${res.user_email} their password + ${res.login_url}`,
        tone: "success",
      });
      setShowCreate(false);
      setForm({ slug: "", name: "", email: "", password: "", sender_title: "" });
      mutate();
    } catch (e) {
      toast.push({
        title: "Create failed",
        description: e instanceof Error ? e.message : String(e),
        tone: "error",
      });
    } finally {
      setCreating(false);
    }
  }

  const [inviting, setInviting] = useState<string | null>(null);
  const [inviteResult, setInviteResult] = useState<InviteResult | null>(null);

  async function inviteFromWaitlist(email: string) {
    setInviting(email);
    try {
      const res = await apiFetch<InviteResult>("/api/v1/admin/waitlist/invite", {
        method: "POST",
        body: { email },
      });
      setInviteResult(res);
      toast.push({
        title: `Account "${res.slug}" created`,
        description: res.welcome_email_sent
          ? `Welcome email sent to ${res.email}`
          : "⚠ Welcome email failed — send the details below manually",
        tone: "success",
      });
      mutate();
      mutateWaitlist();
    } catch (e) {
      toast.push({
        title: "Invite failed",
        description: e instanceof Error ? e.message : String(e),
        tone: "error",
      });
    } finally {
      setInviting(null);
    }
  }

  async function toggle(t: TenantRow) {
    try {
      await apiFetch(`/api/v1/admin/tenants/${t.id}/toggle`, { method: "POST" });
      toast.push({
        title: `${t.slug} ${t.active ? "suspended" : "re-activated"}`,
        tone: t.active ? "neutral" : "success",
      });
      mutate();
    } catch (e) {
      toast.push({
        title: "Toggle failed",
        description: e instanceof Error ? e.message : String(e),
        tone: "error",
      });
    }
  }

  const canCreate =
    form.slug && form.name && form.email && form.password.length >= 8;

  return (
    <>
      <PageHeader
        title="Admin"
        description="Invite-only fleet: accounts, usage, and the waitlist."
        actions={
          <Button variant="primary" onClick={() => setShowCreate((s) => !s)}>
            <Plus size={14} />
            New account
          </Button>
        }
      />

      <div className="px-8 py-6 space-y-6 page-enter">
        {/* Create form */}
        {showCreate && (
          <section className="bg-paper border border-terracotta/40 rounded-xl p-5 shadow-card">
            <div className="font-semibold mb-3">Create invite account</div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>Slug (url-safe, unique)</Label>
                <Input
                  value={form.slug}
                  onChange={(e) =>
                    setForm({ ...form, slug: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "") })
                  }
                  placeholder="ravi"
                />
              </div>
              <div>
                <Label>Full name</Label>
                <Input
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  placeholder="Ravi Kumar"
                />
              </div>
              <div>
                <Label>Login email</Label>
                <Input
                  value={form.email}
                  onChange={(e) => setForm({ ...form, email: e.target.value })}
                  placeholder="ravi@example.com"
                />
              </div>
              <div>
                <Label>Temp password (8+ chars)</Label>
                <Input
                  value={form.password}
                  onChange={(e) => setForm({ ...form, password: e.target.value })}
                  placeholder="they change it later"
                />
              </div>
              <div className="col-span-2">
                <Label>Their consulting title (optional)</Label>
                <Input
                  value={form.sender_title}
                  onChange={(e) => setForm({ ...form, sender_title: e.target.value })}
                  placeholder="Growth Consultant"
                />
              </div>
            </div>
            <div className="flex justify-end gap-2 mt-4">
              <Button variant="ghost" onClick={() => setShowCreate(false)}>
                Cancel
              </Button>
              <Button variant="primary" disabled={!canCreate || creating} onClick={createTenant}>
                {creating ? "Provisioning…" : "Create account"}
              </Button>
            </div>
          </section>
        )}

        {/* Fleet totals */}
        {data && totals ? (
          <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
            <StatCard label="Accounts" value={data.tenants.length} icon={<Users size={14} />} />
            <StatCard label="Leads (all)" value={totals.leads} icon={<ClipboardList size={14} />} />
            <StatCard label="Sends (7d)" value={totals.sends} icon={<Mail size={14} />} />
            <StatCard label="Replies (7d)" value={totals.replies} accent={totals.replies > 0 ? "green" : null} icon={<MessageSquare size={14} />} />
            <StatCard label="LLM cost (7d)" value={`$${totals.cost.toFixed(2)}`} icon={<DollarSign size={14} />} />
          </div>
        ) : null}

        {/* Tenants table */}
        {error ? (
          <ErrorCard message={error.message} onRetry={() => mutate()} />
        ) : isLoading || !data ? (
          <SkeletonRows rows={5} />
        ) : (
          <div className="bg-paper border border-border rounded-xl shadow-card overflow-hidden">
            <table className="w-full text-sm">
              <thead className="border-b border-border bg-cream">
                <tr className="text-left text-[10px] uppercase tracking-wider text-muted">
                  <th className="px-4 py-2.5">Account</th>
                  <th className="px-3 py-2.5">Users</th>
                  <th className="px-3 py-2.5 text-right">Leads</th>
                  <th className="px-3 py-2.5 text-right">Sends 7d</th>
                  <th className="px-3 py-2.5 text-right">Replies 7d</th>
                  <th className="px-3 py-2.5 text-right">LLM $ 7d</th>
                  <th className="px-3 py-2.5">Gmail</th>
                  <th className="px-3 py-2.5 text-right">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {data.tenants.map((t) => (
                  <tr key={t.id} className={cn(!t.active && "opacity-50")}>
                    <td className="px-4 py-3">
                      <div className="font-semibold text-ink">{t.name}</div>
                      <div className="text-xs text-muted">
                        {t.slug} · {t.plan} · joined {relativeTime(t.created_at)}
                      </div>
                    </td>
                    <td className="px-3 py-3 text-xs text-muted">
                      {t.users.length ? t.users.join(", ") : "—"}
                    </td>
                    <td className="px-3 py-3 text-right tabular-nums">{t.leads_total}</td>
                    <td className="px-3 py-3 text-right tabular-nums">{t.sends_7d}</td>
                    <td className="px-3 py-3 text-right tabular-nums">{t.replies_7d}</td>
                    <td className="px-3 py-3 text-right tabular-nums">
                      ${t.llm_cost_7d_usd.toFixed(2)}
                    </td>
                    <td className="px-3 py-3">
                      {t.gmail_connected ? (
                        <span className="text-green text-xs font-semibold">connected</span>
                      ) : (
                        <span className="text-muted text-xs">—</span>
                      )}
                    </td>
                    <td className="px-3 py-3 text-right">
                      <Button size="sm" variant={t.active ? "danger" : "secondary"} onClick={() => toggle(t)}>
                        <Power size={12} />
                        {t.active ? "Suspend" : "Activate"}
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Waitlist */}
        <section className="bg-paper border border-border rounded-xl shadow-card p-5">
          <div className="flex items-center justify-between mb-3">
            <div className="font-semibold">
              Waitlist{" "}
              <span className="text-muted font-normal text-sm">
                ({data?.waitlist_count ?? 0})
              </span>
            </div>
            <Button size="sm" variant="ghost" onClick={() => mutateWaitlist()}>
              Refresh
            </Button>
          </div>
          {/* Credentials reveal after a one-click invite — shown ONCE */}
          {inviteResult && (
            <div className="mb-4 border border-green/30 bg-green-wash/30 rounded-md p-4">
              <div className="text-sm font-semibold text-ink mb-1">
                {inviteResult.welcome_email_sent
                  ? `Account created — welcome email sent to ${inviteResult.email}`
                  : `Account created — send these to ${inviteResult.email}`}
              </div>
              {inviteResult.welcome_email_sent && (
                <div className="text-[11px] text-muted mb-1.5">
                  They&apos;ve been emailed their login + instructions. Credentials below are your backup copy.
                </div>
              )}
              <code className="block text-mono text-[12px] text-ink/90 whitespace-pre-wrap">
                {`Login: ${inviteResult.login_url}\nEmail: ${inviteResult.email}\nPassword: ${inviteResult.password}`}
              </code>
              <div className="flex gap-2 mt-2.5">
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => {
                    navigator.clipboard.writeText(
                      `Login: ${inviteResult.login_url}\nEmail: ${inviteResult.email}\nPassword: ${inviteResult.password}`,
                    );
                    toast.push({ title: "Copied to clipboard", tone: "success" });
                  }}
                >
                  Copy credentials
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setInviteResult(null)}>
                  Dismiss
                </Button>
              </div>
              <p className="text-[11px] text-muted mt-2">
                The password is not stored in plain text — this is the only time
                it&apos;s shown.
              </p>
            </div>
          )}

          {!waitlist || waitlist.length === 0 ? (
            <p className="text-sm text-muted">
              No signups yet — share /welcome to start collecting interest.
            </p>
          ) : (
            <ul className="divide-y divide-border">
              {waitlist.map((w) => (
                <li key={w.email} className="py-3 flex items-start justify-between gap-4">
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium text-ink">
                      {w.name || w.email}
                      {w.name ? (
                        <span className="text-muted font-normal"> · {w.email}</span>
                      ) : null}
                      <span className="text-muted/60 font-normal text-xs ml-2">
                        {relativeTime(w.created_at)}
                      </span>
                    </div>
                    {w.note ? (
                      <div className="text-xs text-ink/70 mt-1">{w.note}</div>
                    ) : null}
                    {/* Qualification chips */}
                    {(w.answers.stage || w.answers.acquisition || w.answers.pipeline_goal) && (
                      <div className="flex flex-wrap gap-1.5 mt-1.5">
                        {w.answers.stage && (
                          <span className="px-1.5 py-0.5 rounded-sm border border-border text-[11px] text-muted">
                            {ANSWER_LABELS[w.answers.stage] ?? w.answers.stage}
                          </span>
                        )}
                        {w.answers.acquisition && (
                          <span className="px-1.5 py-0.5 rounded-sm border border-border text-[11px] text-muted">
                            {ANSWER_LABELS[w.answers.acquisition] ?? w.answers.acquisition}
                          </span>
                        )}
                        {w.answers.pipeline_goal && (
                          <span className="px-1.5 py-0.5 rounded-sm border border-border text-[11px] text-muted">
                            wants {w.answers.pipeline_goal}/mo
                          </span>
                        )}
                      </div>
                    )}
                    {w.answers.challenge ? (
                      <div className="text-xs text-muted mt-1.5 italic">
                        “{w.answers.challenge}”
                      </div>
                    ) : null}
                  </div>
                  <div className="shrink-0">
                    {w.invited_at ? (
                      <span className="text-xs text-green font-medium">
                        Invited {relativeTime(w.invited_at)}
                      </span>
                    ) : (
                      <Button
                        size="sm"
                        variant="primary"
                        disabled={inviting === w.email}
                        onClick={() => inviteFromWaitlist(w.email)}
                      >
                        {inviting === w.email ? "Creating…" : "Create account"}
                      </Button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </>
  );
}
