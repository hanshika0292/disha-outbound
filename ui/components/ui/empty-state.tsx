"use client";

import { ReactNode } from "react";

export function EmptyState({
  title,
  description,
  action,
  actions,
  icon,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  actions?: ReactNode[];
  icon?: ReactNode;
}) {
  const hasMultipleActions = actions && actions.length > 0;

  return (
    <div className="flex flex-col items-center justify-center text-center px-6 py-16 bg-paper border border-border rounded-sm">
      {icon ? <div className="text-muted mb-3">{icon}</div> : null}
      <div className="text-display text-2xl mb-1">{title}</div>
      {description ? (
        <p className="text-sm text-muted max-w-sm mb-6">{description}</p>
      ) : null}
      {hasMultipleActions ? (
        <div className="flex flex-wrap items-stretch justify-center gap-3 w-full max-w-2xl mt-2">
          {actions.map((a, i) => (
            <div key={i} className="flex-1 min-w-[160px] max-w-[220px]">
              {a}
            </div>
          ))}
        </div>
      ) : action ? (
        action
      ) : null}
    </div>
  );
}
