"use client";

import { usePathname } from "next/navigation";
import { Shell } from "./shell";

/** Render the sidebar Shell only outside standalone full-screen pages. */
const BARE_PATHS = ["/login", "/welcome", "/change-password"];

export function ConditionalShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  if (BARE_PATHS.includes(pathname)) {
    return <>{children}</>;
  }
  return <Shell>{children}</Shell>;
}
