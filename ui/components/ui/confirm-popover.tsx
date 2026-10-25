"use client";

import * as PopoverPrimitive from "@radix-ui/react-popover";
import { ReactNode, useState } from "react";
import { Button } from "./button";

export function ConfirmPopover({
  trigger,
  title = "Are you sure?",
  description,
  confirmLabel = "Confirm",
  destructive,
  onConfirm,
}: {
  trigger: ReactNode;
  title?: string;
  description?: string;
  confirmLabel?: string;
  destructive?: boolean;
  onConfirm: () => void | Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [running, setRunning] = useState(false);

  async function run() {
    setRunning(true);
    try {
      await onConfirm();
      setOpen(false);
    } finally {
      setRunning(false);
    }
  }

  return (
    <PopoverPrimitive.Root open={open} onOpenChange={setOpen}>
      <PopoverPrimitive.Trigger asChild>{trigger}</PopoverPrimitive.Trigger>
      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Content
          side="top"
          align="end"
          className="z-50 bg-paper border border-border-strong rounded-sm p-3 w-64 shadow-md"
        >
          <div className="text-sm font-medium text-ink">{title}</div>
          {description ? (
            <p className="text-xs text-muted mt-1">{description}</p>
          ) : null}
          <div className="flex items-center justify-end gap-2 mt-3">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setOpen(false)}
              disabled={running}
            >
              Cancel
            </Button>
            <Button
              size="sm"
              variant={destructive ? "danger" : "primary"}
              onClick={run}
              disabled={running}
            >
              {running ? "…" : confirmLabel}
            </Button>
          </div>
        </PopoverPrimitive.Content>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  );
}
