"use client";

import Link from "next/link";
import { useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import useSWR from "swr";
import {
  LayoutDashboard,
  Users,
  PenLine,
  Send,
  Handshake,
  BarChart3,
  Settings,
  LogOut,
  Zap,
  Inbox,
  Shield,
  Pause,
  Play,
} from "lucide-react";
import { cn } from "@/lib/cn";
import { swrFetcher, clearToken } from "@/lib/api";

type Health = {
  overall: "pass" | "warn" | "fail";
};

type Dashboard = {
  pending_approvals: number;
  pending_drafts: number;
  pending_linkedin: number;
};

const items = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard, key: null },
  { href: "/leads", label: "Leads", icon: Users, key: "pending_approvals" as const },
  { href: "/drafts", label: "Drafts", icon: PenLine, key: "pending_drafts" as const },
  { href: "/inbox", label: "Inbox", icon: Inbox, key: null },
  { href: "/sequence", label: "Sequence", icon: Send, key: null },
  { href: "/linkedin", label: "LinkedIn", icon: Handshake, key: "pending_linkedin" as const },
  { href: "/reports", label: "Reports", icon: BarChart3, key: null },
  { href: "/settings", label: "Settings", icon: Settings, key: null },
];

export function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();

  const { data: dashboard } = useSWR<Dashboard>("/api/v1/dashboard", swrFetcher, {
    refreshInterval: 30_000,
    revalidateOnFocus: true,
  });
  const { data: health } = useSWR<Health>("/health", swrFetcher, {
    refreshInterval: 60_000,
  });
  const { data: me } = useSWR<{
    is_admin: boolean;
    user: { email: string; name: string | null } | null;
    tenant: { slug: string; name: string } | null;
  }>("/api/v1/auth/me", swrFetcher);
  const { data: settings, mutate: mutateSettings } = useSWR<{
    config: Record<string, unknown>;
  }>("/api/v1/settings", swrFetcher);
  const [pausing, setPausing] = useState(false);
  const paused = Boolean(settings?.config?.paused);

  async function togglePause() {
    setPausing(true);
    try {
      const { apiFetch } = await import("@/lib/api");
      await apiFetch("/api/v1/settings", {
        method: "PATCH",
        body: { paused: paused ? "false" : "true" },
      });
      mutateSettings();
    } finally {
      setPausing(false);
    }
  }

  const statusColor =
    (health?.overall === "pass" || (health?.overall as string) === "ok")
      ? "bg-green"
      : health?.overall === "warn"
      ? "bg-warning"
      : health
      ? "bg-red-400"
      : "bg-border-strong";

  async function handleLogout() {
    clearToken();
    await fetch("/api/auth/logout", { method: "POST" });
    router.replace("/login");
    router.refresh();
  }

  const displayName =
    me?.user?.name || me?.tenant?.name || me?.user?.email || "Account";
  const initial = (displayName[0] || "?").toUpperCase();

  return (
    <aside className="w-[240px] shrink-0 bg-paper border-r border-border h-screen sticky top-0 flex flex-col">

      {/* ── Brand ── */}
      <div className="px-5 pt-6 pb-5">
        <div className="flex items-center gap-2.5">
          <div className="w-9 h-9 rounded-xl bg-accent-gradient flex items-center justify-center shrink-0 shadow-accent">
            <Zap size={16} className="text-white" fill="currentColor" />
          </div>
          <div>
            <div className="text-[17px] font-bold tracking-tight leading-none text-ink">
              Disha
            </div>
            <div className="text-[10px] uppercase tracking-[0.1em] text-muted mt-1 font-medium">
              Outbound console
            </div>
          </div>
        </div>
      </div>

      {/* ── Nav ── */}
      <nav className="flex-1 px-3 space-y-0.5 overflow-y-auto scrollbar-thin">
        <div className="text-[10px] uppercase tracking-[0.1em] text-muted/70 font-semibold px-3 pb-1.5 pt-1">
          Workspace
        </div>
        {items.map((it) => {
          const Icon = it.icon;
          const active =
            it.href === "/" ? pathname === "/" : pathname.startsWith(it.href);
          const badge = it.key ? (dashboard?.[it.key] ?? 0) : 0;

          return (
            <Link
              key={it.href}
              href={it.href}
              className={cn(
                "group relative flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-all duration-130",
                active
                  ? "bg-terracotta-wash text-terracotta font-semibold"
                  : "text-ink/70 hover:bg-cream hover:text-ink"
              )}
            >
              {active && (
                <span className="absolute left-0 top-1/2 -translate-y-1/2 w-1 h-5 rounded-r-full bg-terracotta" />
              )}
              <Icon
                size={16}
                className={cn(
                  "shrink-0 transition-colors",
                  active ? "text-terracotta" : "text-muted group-hover:text-ink"
                )}
              />
              <span className="flex-1 truncate">{it.label}</span>
              {badge > 0 && (
                <span
                  className={cn(
                    "min-w-[20px] h-5 px-1.5 flex items-center justify-center",
                    "text-[10px] font-bold rounded-full",
                    active
                      ? "bg-terracotta text-white"
                      : "bg-terracotta-wash text-terracotta"
                  )}
                >
                  {badge}
                </span>
              )}
            </Link>
          );
        })}
      </nav>

      {/* ── User / footer ── */}
      <div className="px-3 pb-4 pt-3 border-t border-border space-y-1">
        {/* Pause / resume — global kill switch */}
        <button
          onClick={togglePause}
          disabled={pausing}
          className={cn(
            "w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm font-medium transition-all",
            paused
              ? "bg-green/10 text-green hover:bg-green/15"
              : "text-ink/70 hover:bg-cream hover:text-ink",
          )}
        >
          {paused ? <Play size={15} /> : <Pause size={15} />}
          <span className="flex-1 text-left">
            {paused ? "Resume sending" : "Pause sending"}
          </span>
        </button>

        {/* API status */}
        <div className="flex items-center gap-2 px-3 py-1.5">
          <span className={cn("w-1.5 h-1.5 rounded-full shrink-0 transition-colors", statusColor)} />
          <span className="text-xs text-muted">
            {!health ? "Checking…" : (health.overall === "pass" || (health.overall as string) === "ok") ? "Operational" : health.overall}
          </span>
        </div>

        {/* Admin console (admins only) */}
        {me?.is_admin ? (
          <Link
            href="/admin"
            className={cn(
              "flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-all",
              pathname.startsWith("/admin")
                ? "bg-terracotta-wash text-terracotta font-semibold"
                : "text-ink/70 hover:bg-cream hover:text-ink",
            )}
          >
            <Shield size={15} className="shrink-0" />
            <span className="flex-1 truncate">Admin</span>
          </Link>
        ) : null}

        {/* User row + logout */}
        <button
          onClick={handleLogout}
          className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm
            text-ink/70 hover:text-ink hover:bg-cream transition-all group"
        >
          <div className="w-7 h-7 rounded-full bg-accent-gradient flex items-center justify-center shrink-0">
            <span className="text-[11px] font-bold text-white">{initial}</span>
          </div>
          <span className="flex-1 text-left truncate font-medium">{displayName}</span>
          <LogOut size={14} className="shrink-0 opacity-0 group-hover:opacity-100 transition-opacity" />
        </button>
      </div>
    </aside>
  );
}
