"use client";

import { AlertTriangle, RefreshCw } from "lucide-react";
import { Button } from "./button";

export function ErrorCard({
  title = "Couldn’t load this",
  message,
  onRetry,
}: {
  title?: string;
  message?: string;
  onRetry?: () => void;
}) {
  return (
    <div className="border border-warning/40 bg-warning-wash/40 rounded-sm p-4 flex items-start gap-3">
      <AlertTriangle size={16} className="text-warning mt-0.5 shrink-0" />
      <div className="flex-1">
        <div className="text-sm font-medium text-ink">{title}</div>
        {message ? (
          <p className="text-xs text-muted mt-1 break-words">{message}</p>
        ) : null}
        {onRetry ? (
          <Button
            size="sm"
            variant="secondary"
            className="mt-2"
            onClick={onRetry}
          >
            <RefreshCw size={12} />
            Retry
          </Button>
        ) : null}
      </div>
    </div>
  );
}

export function ApiOfflineBanner() {
  return (
    <div className="border-b border-warning/40 bg-warning-wash text-warning text-xs px-4 py-1.5">
      API offline — the disha server isn&apos;t responding on{" "}
      <code className="text-mono">{process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000"}</code>.
      Run <code className="text-mono">./dev.sh</code> in a separate terminal.
    </div>
  );
}
