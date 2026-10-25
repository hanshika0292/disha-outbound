"use client";

import { ReactNode, useEffect } from "react";
import { useRouter } from "next/navigation";
import useSWR, { SWRConfig } from "swr";
import { Sidebar } from "./sidebar";
import { ToastProvider } from "@/components/ui/toast";
import { swrFetcher } from "@/lib/api";
import { ApiOfflineBanner } from "@/components/ui/error-card";

export function Shell({ children }: { children: ReactNode }) {
  return (
    <SWRConfig
      value={{
        fetcher: swrFetcher,
        revalidateOnFocus: false,
        shouldRetryOnError: false,
        dedupingInterval: 5_000,
      }}
    >
      <ToastProvider>
        <PasswordResetGuard />
        <div className="flex min-h-screen">
          <Sidebar />
          <main className="flex-1 min-w-0 flex flex-col">
            <ApiHealthBanner />
            <PausedBanner />
            <div className="flex-1">{children}</div>
          </main>
        </div>
      </ToastProvider>
    </SWRConfig>
  );
}

/** Forces admin-provisioned accounts to set a new password before using the
 *  app — covers direct navigation / refresh that bypasses the login redirect. */
function PasswordResetGuard() {
  const router = useRouter();
  const { data } = useSWR<{ must_change_password?: boolean }>(
    "/api/v1/auth/me",
    swrFetcher,
  );
  useEffect(() => {
    if (data?.must_change_password) {
      router.replace("/change-password?forced=1");
    }
  }, [data?.must_change_password, router]);
  return null;
}

function ApiHealthBanner() {
  // Retry a few times before declaring the API offline — the Fly machine can
  // cold-start on the first request after idle, and a single transient failure
  // shouldn't flash the banner. Only show it after retries are exhausted.
  const { error } = useSWR("/health", swrFetcher, {
    refreshInterval: 30_000,
    shouldRetryOnError: true,
    errorRetryCount: 4,
    errorRetryInterval: 2_000,
  });
  if (!error) return null;
  return <ApiOfflineBanner />;
}

function PausedBanner() {
  const { data } = useSWR<{ config: Record<string, unknown> }>(
    "/api/v1/settings",
    swrFetcher,
  );
  if (!data?.config?.paused) return null;
  return (
    <div className="bg-warning-wash border-b border-warning/30 px-8 py-2 text-sm text-warning font-medium">
      Sending is paused — no emails or LinkedIn touches will go out. Resume from
      the sidebar.
    </div>
  );
}
