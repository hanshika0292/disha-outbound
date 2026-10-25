"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  CheckCircle2,
  Settings,
  Users,
  ThumbsUp,
  Send,
  ArrowRight,
  Sparkles,
  Rocket,
  Briefcase,
  Lock,
} from "lucide-react";
import { cn } from "@/lib/cn";

type Persona = "first_timer" | "experienced";

const PERSONA_KEY = "disha_persona";

/* ── Persona-tailored content ─────────────────────────────────────────────── */

const COPY: Record<
  Persona,
  {
    hero: string;
    sub: string;
    steps: { title: string; description: string }[];
  }
> = {
  first_timer: {
    hero: "Land your first consulting client",
    sub: "New to consulting? We'll walk you through it. Tell us who you help, and Disha finds the right people, drafts the outreach, and handles follow-ups — so you can focus on the conversations that matter.",
    steps: [
      {
        title: "Tell us who you help",
        description:
          "Set your name, expertise, and ideal client. This is how Disha introduces you — and who it goes looking for.",
      },
      {
        title: "We find the right people",
        description:
          "Describe your ideal client once. Disha searches the web and surfaces real, matching prospects for you to review.",
      },
      {
        title: "You review & approve",
        description:
          "Every lead is scored against your fit. You stay in control — approve the ones worth pursuing with one click.",
      },
      {
        title: "We handle the follow-ups",
        description:
          "Approved leads get personalised, on-brand outreach automatically. Your first conversations, on autopilot.",
      },
    ],
  },
  experienced: {
    hero: "Put your outreach on autopilot",
    sub: "You've done the work before — now let the machine run it. Define your ICP, plug in your tools, and Disha sources, scores, and sequences while you stay focused on delivery.",
    steps: [
      {
        title: "Define your ICP",
        description:
          "Set your ideal client profile and sender identity. Tight inputs mean sharper, higher-fit leads.",
      },
      {
        title: "Source on autopilot",
        description:
          "Disha pulls matching prospects from the web — founders, operators, decision-makers that fit your criteria.",
      },
      {
        title: "Approve in bulk",
        description:
          "Scored and ranked by fit. Skim, approve the good ones, move on. No manual list-building.",
      },
      {
        title: "Sequences run themselves",
        description:
          "Multi-touch outreach goes out automatically, with replies routed back to you. Outbound on the side.",
      },
    ],
  },
};

const STEP_ICONS = [Settings, Users, ThumbsUp, Send];
const STEP_HREFS = ["/settings", "/leads", "/leads?state=scored", ""];
const STEP_CTAS = ["Set up profile", "Find leads", "Review leads", ""];

/* ── Component ─────────────────────────────────────────────────────────────── */

export function OnboardingChecklist({
  senderConfigured,
  leadsFound,
  leadApproved,
}: {
  senderConfigured: boolean;
  leadsFound: boolean;
  leadApproved: boolean;
}) {
  const [persona, setPersona] = useState<Persona | null>(null);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    const saved = localStorage.getItem(PERSONA_KEY) as Persona | null;
    if (saved === "first_timer" || saved === "experienced") setPersona(saved);
    setHydrated(true);
  }, []);

  function choose(p: Persona) {
    localStorage.setItem(PERSONA_KEY, p);
    setPersona(p);
  }

  // Avoid flashing the wrong screen before localStorage is read
  if (!hydrated) {
    return <div className="h-96" />;
  }

  if (!persona) {
    return <PersonaPicker onChoose={choose} />;
  }

  return (
    <TailoredChecklist
      persona={persona}
      senderConfigured={senderConfigured}
      leadsFound={leadsFound}
      leadApproved={leadApproved}
      onSwitchPersona={() => {
        localStorage.removeItem(PERSONA_KEY);
        setPersona(null);
      }}
    />
  );
}

/* ── Step 0: persona picker ────────────────────────────────────────────────── */

function PersonaPicker({ onChoose }: { onChoose: (p: Persona) => void }) {
  return (
    <div className="max-w-3xl mx-auto page-enter">
      <div className="text-center mb-10">
        <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-terracotta-wash text-terracotta text-xs font-semibold mb-5">
          <Sparkles size={13} />
          Welcome to Disha
        </div>
        <h1 className="text-4xl font-bold tracking-tight text-ink">
          Which best describes you?
        </h1>
        <p className="text-muted mt-3 text-[15px]">
          We&apos;ll tailor your setup so the first run fits how you work.
        </p>
      </div>

      <div className="grid sm:grid-cols-2 gap-4">
        <button
          onClick={() => onChoose("first_timer")}
          className="group text-left bg-paper border border-border rounded-xl p-6 shadow-card
            hover:border-terracotta/40 transition-all"
        >
          <div className="w-11 h-11 rounded-xl bg-terracotta-wash flex items-center justify-center mb-4">
            <Rocket size={20} className="text-terracotta" />
          </div>
          <div className="font-semibold text-ink text-lg">
            I&apos;m starting out
          </div>
          <p className="text-sm text-muted mt-1.5 leading-relaxed">
            New to consulting — help me land my first clients with guidance at
            each step.
          </p>
          <span className="inline-flex items-center gap-1.5 text-terracotta text-sm font-medium mt-4">
            Start here <ArrowRight size={15} />
          </span>
        </button>

        <button
          onClick={() => onChoose("experienced")}
          className="group text-left bg-paper border border-border rounded-xl p-6 shadow-card
            hover:border-terracotta/40 transition-all"
        >
          <div className="w-11 h-11 rounded-xl bg-terracotta-wash flex items-center justify-center mb-4">
            <Briefcase size={20} className="text-terracotta" />
          </div>
          <div className="font-semibold text-ink text-lg">
            I&apos;ve done this before
          </div>
          <p className="text-sm text-muted mt-1.5 leading-relaxed">
            Experienced — I want automation to run outreach on the side while I
            deliver.
          </p>
          <span className="inline-flex items-center gap-1.5 text-terracotta text-sm font-medium mt-4">
            Let&apos;s go <ArrowRight size={15} />
          </span>
        </button>
      </div>
    </div>
  );
}

/* ── Tailored checklist ────────────────────────────────────────────────────── */

function TailoredChecklist({
  persona,
  senderConfigured,
  leadsFound,
  leadApproved,
  onSwitchPersona,
}: {
  persona: Persona;
  senderConfigured: boolean;
  leadsFound: boolean;
  leadApproved: boolean;
  onSwitchPersona: () => void;
}) {
  const copy = COPY[persona];
  const done = [senderConfigured, leadsFound, leadApproved, false];
  const activeStep = !senderConfigured
    ? 1
    : !leadsFound
      ? 2
      : !leadApproved
        ? 3
        : 4;

  const completedCount = done.filter(Boolean).length;
  const progress = (completedCount / 4) * 100;

  return (
    <div className="max-w-4xl mx-auto page-enter">
      {/* Hero */}
      <div className="text-center mb-8">
        <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-terracotta-wash text-terracotta text-xs font-semibold mb-5">
          <Sparkles size={13} />
          {persona === "first_timer" ? "Your guided setup" : "Quick setup"}
        </div>
        <h1 className="text-4xl font-bold tracking-tight text-ink leading-tight">
          {copy.hero}
        </h1>
        <p className="text-muted mt-3 text-[15px] max-w-2xl mx-auto leading-relaxed">
          {copy.sub}
        </p>
      </div>

      {/* Progress bar */}
      <div className="flex items-center gap-3 mb-6 max-w-md mx-auto">
        <div className="flex-1 h-1.5 rounded-full bg-border overflow-hidden">
          <div
            className="h-full bg-accent-gradient rounded-full transition-all duration-500"
            style={{ width: `${progress}%` }}
          />
        </div>
        <span className="text-xs text-muted font-medium tabular-nums">
          {completedCount}/4
        </span>
      </div>

      {/* Steps */}
      <div className="space-y-3">
        {copy.steps.map((step, idx) => {
          const num = idx + 1;
          const Icon = STEP_ICONS[idx];
          const isDone = done[idx];
          const isActive = num === activeStep;
          const isLocked = num > activeStep;
          const href = STEP_HREFS[idx];
          const cta = STEP_CTAS[idx];

          return (
            <div
              key={num}
              className={cn(
                "relative flex items-start gap-4 bg-paper border rounded-xl p-5 transition-all",
                isDone
                  ? "border-green/30"
                  : isActive
                    ? "border-terracotta/40 shadow-card"
                    : "border-border",
                isLocked && "opacity-55",
              )}
            >
              {/* Icon / status */}
              <div
                className={cn(
                  "shrink-0 w-10 h-10 rounded-xl flex items-center justify-center",
                  isDone
                    ? "bg-green-wash text-green"
                    : isActive
                      ? "bg-accent-gradient text-white shadow-accent"
                      : "bg-cream text-muted",
                )}
              >
                {isDone ? (
                  <CheckCircle2 size={20} />
                ) : isLocked ? (
                  <Lock size={16} />
                ) : (
                  <Icon size={18} />
                )}
              </div>

              {/* Text */}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span
                    className={cn(
                      "text-[10px] uppercase tracking-[0.1em] font-bold",
                      isDone
                        ? "text-green"
                        : isActive
                          ? "text-terracotta"
                          : "text-muted/70",
                    )}
                  >
                    Step {num}
                  </span>
                  {isActive && !isDone && (
                    <span className="text-[10px] font-bold text-terracotta bg-terracotta-wash px-1.5 py-0.5 rounded-full">
                      Current step
                    </span>
                  )}
                </div>
                <div
                  className={cn(
                    "font-semibold text-[15px] mt-0.5",
                    isDone ? "text-ink/50 line-through" : "text-ink",
                  )}
                >
                  {step.title}
                </div>
                <p className="text-sm text-muted mt-1 leading-relaxed">
                  {step.description}
                </p>
              </div>

              {/* CTA */}
              <div className="shrink-0 self-center">
                {isDone ? (
                  <span className="inline-flex items-center gap-1 text-xs text-green font-semibold">
                    <CheckCircle2 size={14} />
                    Done
                  </span>
                ) : isActive && href ? (
                  <Link
                    href={href}
                    className="inline-flex items-center gap-1.5 text-sm font-medium text-white
                      bg-terracotta rounded-lg px-4 py-2 shadow-accent hover:brightness-110 transition-all"
                  >
                    {cta}
                    <ArrowRight size={14} />
                  </Link>
                ) : isLocked ? (
                  <span className="text-xs text-muted/60">Locked</span>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>

      {/* Switch persona */}
      <div className="text-center mt-6">
        <button
          onClick={onSwitchPersona}
          className="text-xs text-muted hover:text-ink transition-colors"
        >
          Not quite right? Switch path
        </button>
      </div>
    </div>
  );
}
