import type { Metadata } from "next";
import "./globals.css";
import { ConditionalShell } from "./_components/conditional-shell";

export const metadata: Metadata = {
  title: "Disha — Outbound Console",
  description: "Outbound, automated. A founder-built console for solo consultants.",
};

export const dynamic = "force-dynamic";

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="h-full">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700;800&family=Geist+Mono:wght@400;500;600&display=swap"
        />
      </head>
      <body
        className="min-h-full bg-cream text-ink antialiased"
        suppressHydrationWarning
      >
        <ConditionalShell>{children}</ConditionalShell>
      </body>
    </html>
  );
}
