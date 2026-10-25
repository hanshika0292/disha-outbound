"use client";

import { useState, useRef, useEffect } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { apiFetch, setToken } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const params = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [shake, setShake] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!email || !password || loading) return;
    setLoading(true);
    setError("");

    try {
      // 1. Authenticate against the backend → per-user signed token.
      const res = await apiFetch<{ token: string; must_change_password?: boolean }>(
        "/api/v1/auth/login",
        { method: "POST", body: { email, password } },
      );
      setToken(res.token);
      // 2. Mark the browser session for the page-guard middleware.
      await fetch("/api/auth/login", { method: "POST" });

      // 3. Force a password reset on admin-provisioned (first-login) accounts.
      if (res.must_change_password) {
        router.replace("/change-password?forced=1");
        router.refresh();
        return;
      }

      const from = params.get("from") || "/";
      router.replace(from);
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
      setShake(true);
      setTimeout(() => setShake(false), 500);
      setPassword("");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-cream flex items-center justify-center p-4 relative overflow-hidden">
      <div className="relative w-full max-w-sm">
        {/* Logo area */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-accent-gradient shadow-accent mb-5">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
              <path
                d="M13 2L4.5 13.5H11L9 22L19.5 10.5H13L13 2Z"
                fill="white"
              />
            </svg>
          </div>
          <h1 className="text-3xl font-bold tracking-tight text-ink">Disha</h1>
          <p className="text-muted text-sm mt-1.5">
            Your outbound console
          </p>
        </div>

        {/* Card */}
        <div
          className={`bg-paper border border-border rounded-2xl p-8 ${
            shake ? "animate-shake" : ""
          }`}
        >
          <form onSubmit={handleSubmit} className="space-y-5">
            <div className="space-y-2">
              <label
                htmlFor="email"
                className="block text-xs uppercase tracking-[0.08em] text-muted font-semibold"
              >
                Email
              </label>
              <input
                ref={inputRef}
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                autoComplete="email"
                className="w-full px-4 py-2.5 bg-cream border border-border-strong rounded-lg text-sm text-ink
                  placeholder:text-muted/50 transition-all
                  focus:outline-none focus:border-terracotta focus:ring-4 focus:ring-terracotta-wash"
              />
            </div>
            <div className="space-y-2">
              <label
                htmlFor="password"
                className="block text-xs uppercase tracking-[0.08em] text-muted font-semibold"
              >
                Password
              </label>
              <input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Enter your password"
                autoComplete="current-password"
                className={`w-full px-4 py-2.5 bg-cream border rounded-lg text-sm text-ink
                  placeholder:text-muted/50 transition-all
                  focus:outline-none focus:border-terracotta focus:ring-4 focus:ring-terracotta-wash
                  ${error ? "border-red-400" : "border-border-strong"}`}
              />
              {error && <p className="text-xs text-red-500 mt-1">{error}</p>}
            </div>

            <button
              type="submit"
              disabled={loading || !email || !password}
              className="w-full py-2.5 bg-terracotta text-white text-sm font-semibold
                rounded-md
                hover:bg-terracotta/90
                disabled:opacity-50 disabled:cursor-not-allowed disabled:shadow-none"
            >
              {loading ? (
                <span className="flex items-center justify-center gap-2">
                  <span className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  Signing in…
                </span>
              ) : (
                "Sign in"
              )}
            </button>
          </form>
        </div>

        <p className="text-center text-xs text-muted/70 mt-6">
          Invite-only ·{" "}
          <Link href="/welcome" className="text-terracotta hover:underline">
            Join the waitlist
          </Link>
        </p>
      </div>
    </div>
  );
}
