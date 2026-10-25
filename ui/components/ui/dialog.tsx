"use client";

import * as DialogPrimitive from "@radix-ui/react-dialog";
import { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { X } from "lucide-react";

export function Dialog({
  open,
  onOpenChange,
  children,
  title,
  description,
  size = "md",
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  size?: "md" | "lg" | "xl";
  children: ReactNode;
}) {
  const widthCls = {
    md: "max-w-md",
    lg: "max-w-xl",
    xl: "max-w-3xl",
  }[size];
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-ink/30 backdrop-blur-sm" />
        <DialogPrimitive.Content
          className={cn(
            "fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-50",
            "bg-paper border border-border-strong rounded-xl shadow-pop w-[92vw]",
            // Cap height to the viewport and lay out as a column so the header
            // stays fixed and the body scrolls — otherwise tall content (e.g.
            // a long candidate list) pushes the header/footer off-screen.
            "flex flex-col max-h-[88vh]",
            widthCls,
          )}
        >
          <div className="flex items-start justify-between px-5 py-4 border-b border-border shrink-0">
            <div>
              <DialogPrimitive.Title className="text-lg font-semibold tracking-tight">
                {title}
              </DialogPrimitive.Title>
              {description ? (
                <DialogPrimitive.Description className="text-xs text-muted mt-1">
                  {description}
                </DialogPrimitive.Description>
              ) : null}
            </div>
            <DialogPrimitive.Close className="text-muted hover:text-ink shrink-0 ml-4">
              <X size={16} />
            </DialogPrimitive.Close>
          </div>
          <div className="p-5 flex-1 min-h-0 overflow-y-auto scrollbar-thin">
            {children}
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

/** Right-side drawer. Same Radix Dialog primitives, different positioning. */
export function Drawer({
  open,
  onOpenChange,
  children,
  title,
  description,
  widthClass = "w-[480px]",
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title?: string;
  description?: string;
  widthClass?: string;
  children: ReactNode;
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-ink/30 backdrop-blur-sm" />
        <DialogPrimitive.Content
          className={cn(
            "fixed top-0 right-0 bottom-0 z-50 bg-paper border-l border-border-strong overflow-y-auto scrollbar-thin",
            widthClass,
          )}
        >
          {title ? (
            <div className="flex items-start justify-between px-5 py-4 border-b border-border sticky top-0 bg-paper z-10">
              <div>
                <DialogPrimitive.Title className="text-display text-xl">
                  {title}
                </DialogPrimitive.Title>
                {description ? (
                  <DialogPrimitive.Description className="text-xs text-muted mt-1">
                    {description}
                  </DialogPrimitive.Description>
                ) : null}
              </div>
              <DialogPrimitive.Close className="text-muted hover:text-ink">
                <X size={16} />
              </DialogPrimitive.Close>
            </div>
          ) : null}
          <div className="p-5">{children}</div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
