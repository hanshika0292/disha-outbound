"use client";

import { ReactNode } from "react";

export function PageHeader({
  title,
  description,
  actions,
  badge,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
  badge?: ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-6 px-8 pt-6 pb-4 border-b border-border bg-cream sticky top-0 z-10">
      <div className="min-w-0">
        <div className="flex items-baseline gap-3">
          <h1 className="text-[22px] font-semibold tracking-tight leading-tight text-ink truncate">
            {title}
          </h1>
          {badge}
        </div>
        {description && (
          <p className="text-[13px] text-muted mt-0.5 max-w-xl truncate">
            {description}
          </p>
        )}
      </div>
      {actions && (
        <div className="flex items-center gap-2 shrink-0">{actions}</div>
      )}
    </div>
  );
}
