"use client";

import { useState } from "react";
import { Search, Check } from "lucide-react";
import { Dialog } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/inputs";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";
import { apiFetch } from "@/lib/api";
import { cn } from "@/lib/cn";

type Candidate = {
  first_name: string;
  last_name: string;
  email: string | null;
  title: string;
  company: string;
  country: string;
  industry: string | null;
  funding_stage: string | null;
  employee_count: number | null;
  linkedin_url: string | null;
  source_url: string | null;
  raw: Record<string, unknown>;
};

type PreviewResponse = {
  provider: string;
  query: {
    titles: string[];
    industries: string[];
    countries: string[];
    funding_stages: string[];
    employee_min: number | null;
    employee_max: number | null;
    keywords: string[];
    limit: number;
  };
  candidates: Candidate[];
};

type RunResult = {
  provider: string;
  found: number;
  added: number;
  duplicates: number;
  skipped_no_email: number;
  scored: number;
};

const csv = (s: string): string[] =>
  s.split(",").map((x) => x.trim()).filter(Boolean);

function EmailConfidenceBadge({ confidence }: { confidence: string | null }) {
  if (confidence === "verified")
    return (
      <span className="shrink-0 px-1.5 py-px rounded-full bg-green-wash text-green text-[10px] font-bold">
        Verified
      </span>
    );
  if (confidence === "guessed")
    return (
      <span className="shrink-0 px-1.5 py-px rounded-full bg-warning-wash text-warning text-[10px] font-bold">
        Guessed
      </span>
    );
  if (confidence === "risky")
    return (
      <span className="shrink-0 px-1.5 py-px rounded-full bg-cream text-muted text-[10px] font-bold">
        Unverified
      </span>
    );
  return null;
}

export function FindLeadsModal({
  open,
  onOpenChange,
  onSuccess,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onSuccess: () => void;
}) {
  const toast = useToast();
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [autoScore, setAutoScore] = useState(true);
  const [runWeekly, setRunWeekly] = useState(false);
  const [previewed, setPreviewed] = useState(false);
  const [provider, setProvider] = useState<"web" | "mock">("web");

  // Criteria — empty means "use ICP defaults". Pre-filled from preview response.
  const [titles, setTitles] = useState("");
  const [industries, setIndustries] = useState("");
  const [countries, setCountries] = useState("");
  const [stages, setStages] = useState("");
  const [empMin, setEmpMin] = useState("");
  const [empMax, setEmpMax] = useState("");
  const [limit, setLimit] = useState("25");

  const keyOf = (c: Candidate) => c.email ?? `${c.first_name}-${c.company}`;

  function buildOverrides(): Record<string, unknown> {
    const o: Record<string, unknown> = {};
    if (titles) o.titles = csv(titles);
    if (industries) o.industries = csv(industries);
    if (countries) o.countries = csv(countries);
    if (stages) o.funding_stages = csv(stages);
    if (empMin) o.employee_min = Number(empMin);
    if (empMax) o.employee_max = Number(empMax);
    return o;
  }

  async function preview() {
    setLoading(true);
    try {
      const res = await apiFetch<PreviewResponse>("/api/v1/sourcing/preview", {
        method: "POST",
        body: {
          provider,
          overrides: buildOverrides(),
          limit: Number(limit) || 25,
        },
      });
      setCandidates(res.candidates);
      setSelected(new Set(res.candidates.map(keyOf)));
      setPreviewed(true);
      // Backfill the criteria inputs from the ICP-derived query the first time.
      if (!titles && res.query.titles.length) setTitles(res.query.titles.join(", "));
      if (!industries && res.query.industries.length)
        setIndustries(res.query.industries.join(", "));
      if (!countries && res.query.countries.length)
        setCountries(res.query.countries.join(", "));
      if (!stages && res.query.funding_stages.length)
        setStages(res.query.funding_stages.join(", "));
      if (!empMin && res.query.employee_min != null)
        setEmpMin(String(res.query.employee_min));
      if (!empMax && res.query.employee_max != null)
        setEmpMax(String(res.query.employee_max));
    } catch (e) {
      toast.push({
        title: "Preview failed",
        description: e instanceof Error ? e.message : String(e),
        tone: "error",
      });
    } finally {
      setLoading(false);
    }
  }

  async function run() {
    const chosen = candidates.filter((c) => selected.has(keyOf(c)));
    if (chosen.length === 0) return;
    setRunning(true);
    try {
      const res = await apiFetch<RunResult>("/api/v1/sourcing/run", {
        method: "POST",
        body: {
          provider,
          candidates: chosen,
          auto_score: autoScore,
        },
      });
      // Persist the saved search for the weekly worker run (best-effort).
      if (runWeekly) {
        try {
          await apiFetch("/api/v1/settings", {
            method: "PATCH",
            body: {
              auto_source: JSON.stringify({
                enabled: true,
                provider,
                overrides: buildOverrides(),
                weekly_limit: Number(limit) || 50,
              }),
            },
          });
        } catch {
          /* non-fatal — the leads were still added */
        }
      }
      toast.push({
        title: `Added ${res.added} leads`,
        description: `${res.duplicates} already existed${autoScore ? `, ${res.scored} scored` : ""}${runWeekly ? " · weekly auto-sourcing on" : ""}`,
        tone: "success",
      });
      onSuccess();
      reset();
      onOpenChange(false);
    } catch (e) {
      toast.push({
        title: "Add failed",
        description: e instanceof Error ? e.message : String(e),
        tone: "error",
      });
    } finally {
      setRunning(false);
    }
  }

  function reset() {
    setCandidates([]);
    setSelected(new Set());
    setPreviewed(false);
    setTitles("");
    setIndustries("");
    setCountries("");
    setStages("");
    setEmpMin("");
    setEmpMax("");
    setLimit("25");
  }

  function toggle(k: string) {
    setSelected((cur) => {
      const next = new Set(cur);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });
  }

  const allSelected = candidates.length > 0 && selected.size === candidates.length;

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        onOpenChange(o);
        if (!o) reset();
      }}
      title="Find leads"
      description="Criteria are pre-filled from your ICP. Edit, preview, then add the ones you want — they land in Scored · awaiting approval."
      size="xl"
    >
      <div className="space-y-4">
        {/* provider toggle */}
        <div className="flex items-center gap-2 text-sm">
          <span className="text-muted">Source:</span>
          <button
            onClick={() => { setProvider("web"); setPreviewed(false); setCandidates([]); }}
            className={cn(
              "px-3 py-1 rounded-sm border text-xs font-medium transition-colors",
              provider === "web"
                ? "bg-terracotta text-cream border-terracotta"
                : "border-border-strong text-muted hover:text-ink"
            )}
          >
            Web research
          </button>
          <button
            onClick={() => { setProvider("mock"); setPreviewed(false); setCandidates([]); }}
            className={cn(
              "px-3 py-1 rounded-sm border text-xs font-medium transition-colors",
              provider === "mock"
                ? "bg-terracotta text-cream border-terracotta"
                : "border-border-strong text-muted hover:text-ink"
            )}
          >
            Sample data
          </button>
          {provider === "web" && (
            <span className="text-xs text-muted ml-1">
              Powered by Google Search + AI extraction
            </span>
          )}
        </div>
        {/* criteria */}
        <div className="grid grid-cols-2 gap-3">
          <div className="col-span-2">
            <Label>Titles (comma-separated)</Label>
            <Input
              value={titles}
              onChange={(e) => setTitles(e.target.value)}
              placeholder="Founder, CEO, CTO, Head of Data"
            />
          </div>
          <div className="col-span-2">
            <Label>Industries</Label>
            <Input
              value={industries}
              onChange={(e) => setIndustries(e.target.value)}
              placeholder="Artificial Intelligence, SaaS"
            />
          </div>
          <div>
            <Label>Countries</Label>
            <Input
              value={countries}
              onChange={(e) => setCountries(e.target.value)}
              placeholder="IN, US, UK, SG"
            />
          </div>
          <div>
            <Label>Funding stages</Label>
            <Input
              value={stages}
              onChange={(e) => setStages(e.target.value)}
              placeholder="series_a, series_b"
            />
          </div>
          <div>
            <Label>Employees min</Label>
            <Input
              type="number"
              value={empMin}
              onChange={(e) => setEmpMin(e.target.value)}
            />
          </div>
          <div>
            <Label>Employees max</Label>
            <Input
              type="number"
              value={empMax}
              onChange={(e) => setEmpMax(e.target.value)}
            />
          </div>
          <div>
            <Label>Limit</Label>
            <Input
              type="number"
              value={limit}
              onChange={(e) => setLimit(e.target.value)}
            />
          </div>
          <div className="flex items-end">
            <Button
              variant="secondary"
              className="w-full"
              onClick={preview}
              disabled={loading}
            >
              <Search size={14} />
              {loading ? "Searching…" : previewed ? "Re-preview" : "Preview"}
            </Button>
          </div>
        </div>

        {/* results */}
        {loading ? (
          <div className="space-y-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : previewed ? (
          candidates.length === 0 ? (
            <div className="text-sm text-muted py-6 text-center border border-border rounded-sm">
              No candidates matched. Loosen the criteria and preview again.
            </div>
          ) : (
            <div className="border border-border rounded-sm overflow-hidden">
              <div className="flex items-center justify-between px-3 py-2 bg-cream border-b border-border text-xs">
                <button
                  className="text-terracotta font-medium"
                  onClick={() =>
                    setSelected(
                      allSelected
                        ? new Set()
                        : new Set(candidates.map(keyOf)),
                    )
                  }
                >
                  {allSelected ? "Deselect all" : "Select all"}
                </button>
                <span className="text-muted">
                  {selected.size} of {candidates.length} selected
                </span>
              </div>
              <ul className="divide-y divide-border">
                {candidates.map((c) => {
                  const k = keyOf(c);
                  const on = selected.has(k);
                  return (
                    <li
                      key={k}
                      onClick={() => toggle(k)}
                      className={cn(
                        "flex items-center gap-3 px-3 py-2 cursor-pointer text-sm",
                        on ? "bg-terracotta-wash/40" : "hover:bg-cream",
                      )}
                    >
                      <span
                        className={cn(
                          "w-4 h-4 rounded-sm border flex items-center justify-center shrink-0",
                          on
                            ? "bg-terracotta border-terracotta text-cream"
                            : "border-border-strong",
                        )}
                      >
                        {on ? <Check size={11} /> : null}
                      </span>
                      <div className="flex-1 min-w-0">
                        <div className="font-medium text-ink truncate flex items-center gap-2">
                          {c.first_name} {c.last_name}
                          <span className="text-muted font-normal">· {c.title}</span>
                          {c.linkedin_url && (
                            <a
                              href={c.linkedin_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              onClick={(e) => e.stopPropagation()}
                              className="text-[10px] text-blue-600 underline shrink-0"
                            >
                              LinkedIn
                            </a>
                          )}
                        </div>
                        <div className="text-xs text-muted truncate flex items-center gap-1.5">
                          <span className="truncate">
                            {c.company} · {c.country}
                            {c.funding_stage ? ` · ${c.funding_stage}` : ""}
                            {c.employee_count ? ` · ${c.employee_count} ppl` : ""}
                            {c.email ? ` · ${c.email}` : ""}
                          </span>
                          {c.email ? (
                            <EmailConfidenceBadge
                              confidence={
                                (c.raw?.email_confidence as string) ?? null
                              }
                            />
                          ) : (
                            <span className="text-amber-600 shrink-0">
                              no email
                            </span>
                          )}
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ul>
            </div>
          )
        ) : (
          <div className="text-sm text-muted py-6 text-center border border-dashed border-border rounded-sm">
            Click Preview to pull candidates matching your ICP.
          </div>
        )}

        {/* footer — sticky so the Add button stays reachable while the
            candidate list scrolls above it */}
        <div className="sticky bottom-0 -mx-5 -mb-5 px-5 py-3 bg-paper/95 backdrop-blur-sm border-t border-border flex items-center justify-between">
          <div className="flex items-center gap-4">
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input
                type="checkbox"
                checked={autoScore}
                onChange={(e) => setAutoScore(e.target.checked)}
                className="accent-terracotta"
              />
              <span>Auto-score</span>
            </label>
            <label
              className="flex items-center gap-2 text-sm cursor-pointer"
              title="The worker re-runs this exact search every week, dedupes against existing leads, and queues new matches for your review."
            >
              <input
                type="checkbox"
                checked={runWeekly}
                onChange={(e) => setRunWeekly(e.target.checked)}
                className="accent-terracotta"
              />
              <span>Repeat weekly</span>
            </label>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              disabled={running || selected.size === 0}
              onClick={run}
            >
              {running ? "Adding…" : `Add ${selected.size} lead${selected.size === 1 ? "" : "s"}`}
            </Button>
          </div>
        </div>
      </div>
    </Dialog>
  );
}
