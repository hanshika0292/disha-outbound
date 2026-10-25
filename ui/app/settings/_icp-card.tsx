"use client";

import { useState } from "react";
import useSWR from "swr";
import { Target, Sparkles, CheckCircle2, FileText, ListChecks } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/inputs";
import { useToast } from "@/components/ui/toast";
import { apiFetch } from "@/lib/api";

type IcpResponse = {
  version: number | null;
  config: {
    name?: string;
    dimensions?: Array<{ key: string; label: string; weight: number }>;
  } | null;
};

const QUESTIONS = [
  { key: "expertise", label: "What's your expertise?", placeholder: "e.g. Product manager, 8 years in B2B SaaS, strong in onboarding and activation…", required: true },
  { key: "problems_solved", label: "What problems do you solve?", placeholder: "e.g. Startups whose signups don't convert; teams shipping features nobody uses…", required: true },
  { key: "past_clients", label: "Who have you worked with? (optional)", placeholder: "e.g. Two seed-stage fintechs and a Series-A healthtech — or leave blank", required: false },
  { key: "outcomes", label: "What outcomes can you point to? (optional)", placeholder: "e.g. Grew activation 22% in 3 months; cut onboarding dropoff in half…", required: false },
  { key: "target_countries", label: "Target countries (optional)", placeholder: "e.g. India, US, Singapore", required: false },
  { key: "target_stages", label: "Target funding stages (optional)", placeholder: "e.g. seed to Series B", required: false },
] as const;

export function IcpWizardCard() {
  const { data, mutate } = useSWR<IcpResponse>("/api/v1/settings/icp");
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"auto" | "manual">("auto");
  const [generating, setGenerating] = useState(false);

  // manual interview
  const [answers, setAnswers] = useState<Record<string, string>>({});
  // auto-from-profile
  const [profile, setProfile] = useState("");
  const [linkedin, setLinkedin] = useState("");
  const [targets, setTargets] = useState({ target_countries: "", target_stages: "", target_sizes: "" });

  const canManual = Boolean(answers.expertise?.trim() && answers.problems_solved?.trim());
  const canAuto = profile.trim().length >= 40;

  function onSuccess(version: number, extra?: string) {
    toast.push({
      title: `ICP v${version} generated & active`,
      description: extra ?? "Sourcing, scoring, and drafts now target this profile.",
      tone: "success",
    });
    setOpen(false);
    setAnswers({});
    setProfile("");
    setLinkedin("");
    setTargets({ target_countries: "", target_stages: "", target_sizes: "" });
    mutate();
  }

  async function generateManual() {
    setGenerating(true);
    try {
      const res = await apiFetch<{ version: number }>("/api/v1/settings/icp/generate", {
        method: "POST",
        body: { target_sizes: "", ...answers },
      });
      onSuccess(res.version);
    } catch (e) {
      toast.push({ title: "Generation failed", description: e instanceof Error ? e.message : String(e), tone: "error" });
    } finally {
      setGenerating(false);
    }
  }

  async function generateAuto() {
    setGenerating(true);
    try {
      const res = await apiFetch<{ version: number; persona_filled: Record<string, string> }>(
        "/api/v1/settings/icp/generate-from-profile",
        { method: "POST", body: { profile_text: profile, linkedin_url: linkedin, ...targets } },
      );
      const filled = Object.keys(res.persona_filled || {});
      onSuccess(
        res.version,
        filled.length
          ? `ICP built. Also auto-filled your ${filled.map(prettyKey).join(", ")} for the email signature.`
          : "Sourcing, scoring, and drafts now target this profile.",
      );
    } catch (e) {
      toast.push({ title: "Generation failed", description: e instanceof Error ? e.message : String(e), tone: "error" });
    } finally {
      setGenerating(false);
    }
  }

  return (
    <section className="bg-paper border border-border rounded-sm p-5">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="text-display text-xl mb-1 flex items-center gap-2">
            <Target size={17} className="text-terracotta" />
            Ideal client profile
          </div>
          <p className="text-xs text-muted">
            Drives who Disha finds, how leads are scored, and what the emails say.
            Paste your resume or LinkedIn and AI builds it for you.
          </p>
          {data?.config ? (
            <div className="mt-3 text-sm">
              <span className="inline-flex items-center gap-1.5 text-green font-medium">
                <CheckCircle2 size={14} />
                {data.config.name || "Active ICP"} · v{data.version}
              </span>
              <div className="flex flex-wrap gap-1.5 mt-2">
                {(data.config.dimensions ?? []).map((d) => (
                  <span key={d.key} className="px-2 py-0.5 rounded-full bg-cream border border-border text-[11px] text-muted">
                    {d.label} · {Math.round(d.weight * 100)}%
                  </span>
                ))}
              </div>
            </div>
          ) : null}
        </div>
        {!open && (
          <Button variant="secondary" onClick={() => setOpen(true)}>
            {data?.config ? "Redefine" : "Define ICP"}
          </Button>
        )}
      </div>

      {open && (
        <div className="mt-5 pt-5 border-t border-border space-y-4">
          {/* Mode switch */}
          <div className="inline-flex rounded-md border border-border overflow-hidden text-sm">
            <button
              onClick={() => setMode("auto")}
              className={`flex items-center gap-1.5 px-3 py-1.5 ${mode === "auto" ? "bg-terracotta text-white" : "bg-cream text-muted hover:text-ink"}`}
            >
              <FileText size={13} /> From resume / LinkedIn
            </button>
            <button
              onClick={() => setMode("manual")}
              className={`flex items-center gap-1.5 px-3 py-1.5 border-l border-border ${mode === "manual" ? "bg-terracotta text-white" : "bg-cream text-muted hover:text-ink"}`}
            >
              <ListChecks size={13} /> Answer questions
            </button>
          </div>

          {mode === "auto" ? (
            <>
              <div>
                <Label>
                  Paste your resume or LinkedIn profile
                  <span className="text-terracotta"> *</span>
                </Label>
                <textarea
                  value={profile}
                  onChange={(e) => setProfile(e.target.value)}
                  placeholder="Paste your resume text, or copy your LinkedIn 'About' section + experience. The more detail, the sharper your ICP."
                  rows={7}
                  className="w-full px-3 py-2 bg-cream border border-border rounded-sm text-sm leading-relaxed resize-y focus:outline-none focus:border-terracotta"
                />
                <p className="text-[11px] text-muted mt-1">
                  AI reads this to build your ICP and auto-fill your email signature (name, title, credentials).
                </p>
              </div>
              <div>
                <Label>LinkedIn URL (optional)</Label>
                <input
                  value={linkedin}
                  onChange={(e) => setLinkedin(e.target.value)}
                  placeholder="https://linkedin.com/in/you"
                  className="w-full px-3 py-2 bg-cream border border-border rounded-sm text-sm focus:outline-none focus:border-terracotta"
                />
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <TargetInput label="Target countries" value={targets.target_countries} onChange={(v) => setTargets((t) => ({ ...t, target_countries: v }))} placeholder="India, US" />
                <TargetInput label="Funding stages" value={targets.target_stages} onChange={(v) => setTargets((t) => ({ ...t, target_stages: v }))} placeholder="seed–Series B" />
                <TargetInput label="Company sizes" value={targets.target_sizes} onChange={(v) => setTargets((t) => ({ ...t, target_sizes: v }))} placeholder="10–200" />
              </div>
              <div className="flex items-center justify-end gap-2">
                <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
                <Button variant="primary" onClick={generateAuto} disabled={!canAuto || generating}>
                  <Sparkles size={14} />
                  {generating ? "Reading your background…" : "Auto-generate from my background"}
                </Button>
              </div>
            </>
          ) : (
            <>
              {QUESTIONS.map((q) => (
                <div key={q.key}>
                  <Label>
                    {q.label}
                    {q.required ? <span className="text-terracotta"> *</span> : null}
                  </Label>
                  <textarea
                    value={answers[q.key] ?? ""}
                    onChange={(e) => setAnswers((cur) => ({ ...cur, [q.key]: e.target.value }))}
                    placeholder={q.placeholder}
                    rows={q.key === "expertise" || q.key === "problems_solved" ? 3 : 2}
                    className="w-full px-3 py-2 bg-cream border border-border rounded-sm text-sm leading-relaxed resize-y focus:outline-none focus:border-terracotta"
                  />
                </div>
              ))}
              <div className="flex items-center justify-end gap-2">
                <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
                <Button variant="primary" onClick={generateManual} disabled={!canManual || generating}>
                  <Sparkles size={14} />
                  {generating ? "Generating…" : "Generate my ICP"}
                </Button>
              </div>
            </>
          )}
        </div>
      )}
    </section>
  );
}

function prettyKey(k: string): string {
  return (
    { sender_name: "name", sender_title: "title", sender_credentials: "credentials", linkedin_url: "LinkedIn" } as Record<string, string>
  )[k] ?? k;
}

function TargetInput({ label, value, onChange, placeholder }: { label: string; value: string; onChange: (v: string) => void; placeholder: string }) {
  return (
    <div>
      <Label>{label}</Label>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full px-3 py-2 bg-cream border border-border rounded-sm text-sm focus:outline-none focus:border-terracotta"
      />
    </div>
  );
}
