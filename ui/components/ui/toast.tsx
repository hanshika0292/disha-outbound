"use client";

import {
  ReactNode,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { cn } from "@/lib/cn";

type Toast = {
  id: number;
  title: string;
  description?: string;
  tone?: "neutral" | "success" | "error";
};

type ToastCtx = {
  push: (t: Omit<Toast, "id">) => void;
};

const ToastContext = createContext<ToastCtx | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const push = useCallback((t: Omit<Toast, "id">) => {
    setToasts((cur) => [...cur, { ...t, id: Date.now() + Math.random() }]);
  }, []);

  useEffect(() => {
    if (!toasts.length) return;
    const t = setTimeout(() => {
      setToasts((cur) => cur.slice(1));
    }, 4000);
    return () => clearTimeout(t);
  }, [toasts]);

  const ctx = useMemo(() => ({ push }), [push]);

  return (
    <ToastContext.Provider value={ctx}>
      {children}
      <div className="fixed top-4 right-4 z-50 flex flex-col gap-2 max-w-sm pointer-events-none">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={cn(
              "pointer-events-auto bg-paper border rounded-sm px-3 py-2 shadow-sm",
              t.tone === "success" && "border-green/40",
              t.tone === "error" && "border-terracotta/60",
              (!t.tone || t.tone === "neutral") && "border-border-strong",
            )}
          >
            <div className="text-sm font-medium text-ink">{t.title}</div>
            {t.description ? (
              <div className="text-xs text-muted mt-0.5">{t.description}</div>
            ) : null}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastCtx {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used inside <ToastProvider>");
  return ctx;
}
