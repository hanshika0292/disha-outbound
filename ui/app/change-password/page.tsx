"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { apiFetch } from "@/lib/api";

function ChangePasswordInner() {
  const router = useRouter();
  const params = useSearchParams();
  const forced = params.get("forced") === "1";

  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (next.length < 8) {
      setError("Password must be at least 8 characters");
      return;
    }
    if (next !== confirm) {
      setError("Passwords don't match");
      return;
    }
    setLoading(true);
    try {
      await apiFetch("/api/v1/auth/change-password", {
        method: "POST",
        body: forced
          ? { new_password: next }
          : { current_password: current, new_password: next },
      });
      router.replace("/");
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't update password");
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-cream flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-accent-gradient shadow-accent mb-5">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
              <path d="M13 2L4.5 13.5H11L9 22L19.5 10.5H13L13 2Z" fill="white" />
            </svg>
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-ink">
            {forced ? "Set your password" : "Change password"}
          </h1>
          <p className="text-muted text-sm mt-1.5">
            {forced
              ? "Choose a new password to secure your account."
              : "Update the password for your account."}
          </p>
        </div>

        <div className="bg-paper border border-border rounded-2xl p-8">
          <form onSubmit={handleSubmit} className="space-y-5">
            {!forced && (
              <Field
                id="current"
                label="Current password"
                value={current}
                onChange={setCurrent}
                autoComplete="current-password"
                placeholder="Current password"
              />
            )}
            <Field
              id="next"
              label="New password"
              value={next}
              onChange={setNext}
              autoComplete="new-password"
              placeholder="At least 8 characters"
            />
            <Field
              id="confirm"
              label="Confirm new password"
              value={confirm}
              onChange={setConfirm}
              autoComplete="new-password"
              placeholder="Re-enter new password"
              error={Boolean(error)}
            />
            {error && <p className="text-xs text-red-500 -mt-2">{error}</p>}

            <button
              type="submit"
              disabled={loading || !next || !confirm || (!forced && !current)}
              className="w-full py-2.5 bg-terracotta text-white text-sm font-semibold rounded-md
                hover:bg-terracotta/90 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {loading ? "Saving…" : forced ? "Set password & continue" : "Update password"}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}

function Field({
  id,
  label,
  value,
  onChange,
  autoComplete,
  placeholder,
  error,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (v: string) => void;
  autoComplete: string;
  placeholder: string;
  error?: boolean;
}) {
  return (
    <div className="space-y-2">
      <label
        htmlFor={id}
        className="block text-xs uppercase tracking-[0.08em] text-muted font-semibold"
      >
        {label}
      </label>
      <input
        id={id}
        type="password"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        autoComplete={autoComplete}
        className={`w-full px-4 py-2.5 bg-cream border rounded-lg text-sm text-ink
          placeholder:text-muted/50 transition-all
          focus:outline-none focus:border-terracotta focus:ring-4 focus:ring-terracotta-wash
          ${error ? "border-red-400" : "border-border-strong"}`}
      />
    </div>
  );
}

export default function ChangePasswordPage() {
  return (
    <Suspense fallback={null}>
      <ChangePasswordInner />
    </Suspense>
  );
}
