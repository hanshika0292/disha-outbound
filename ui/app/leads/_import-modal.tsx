"use client";

import { useState } from "react";
import { Dialog } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/inputs";
import { useToast } from "@/components/ui/toast";
import { apiFetch } from "@/lib/api";

type ImportResult = {
  total_rows: number;
  created: number;
  duplicates: number;
  errors: number;
  error_rows?: Array<{ row: number; reason: string }>;
};

export function ImportModal({
  open,
  onOpenChange,
  onSuccess,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onSuccess: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [autoScore, setAutoScore] = useState(false);
  const [dryRun, setDryRun] = useState(true);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const toast = useToast();

  function reset() {
    setFile(null);
    setResult(null);
    setAutoScore(false);
    setDryRun(true);
  }

  async function submit() {
    if (!file) return;
    setSubmitting(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await apiFetch<ImportResult>("/api/v1/leads/import", {
        method: "POST",
        formData: fd,
        params: { auto_score: autoScore, dry_run: dryRun },
      });
      setResult(res);
      if (!dryRun) {
        toast.push({
          title: `Imported ${res.created} leads`,
          description: `${res.duplicates} duplicates, ${res.errors} errors`,
          tone: res.errors === 0 ? "success" : "neutral",
        });
        onSuccess();
      }
    } catch (e) {
      toast.push({
        title: "Import failed",
        description: e instanceof Error ? e.message : String(e),
        tone: "error",
      });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        onOpenChange(o);
        if (!o) reset();
      }}
      title="Import leads from CSV"
      description={
        "Required columns: first_name, last_name, email, title, company, country. " +
        "Optional: linkedin_url, notes, source_url, funding_stage, employee_count."
      }
      size="lg"
    >
      <div className="space-y-4">
        <div>
          <Label htmlFor="csv-upload">CSV file</Label>
          <input
            id="csv-upload"
            type="file"
            accept=".csv"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="text-sm text-ink file:mr-3 file:rounded-sm file:border file:border-border-strong file:bg-paper file:px-3 file:py-1.5 file:text-xs file:font-medium file:cursor-pointer"
          />
        </div>

        <div className="flex items-center gap-4 text-sm">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={dryRun}
              onChange={(e) => setDryRun(e.target.checked)}
              className="accent-terracotta"
            />
            <span>Dry run (don&apos;t save)</span>
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={autoScore}
              onChange={(e) => setAutoScore(e.target.checked)}
              className="accent-terracotta"
            />
            <span>Auto-score after import</span>
          </label>
        </div>

        {result ? (
          <div className="bg-cream border border-border rounded-sm p-3 text-sm">
            <div className="text-display text-lg mb-1">
              {dryRun ? "Dry run result" : "Imported"}
            </div>
            <div className="grid grid-cols-2 gap-2 text-xs text-ink">
              <div>Total rows: {result.total_rows}</div>
              <div>Created: {result.created}</div>
              <div>Duplicates: {result.duplicates}</div>
              <div>Errors: {result.errors}</div>
            </div>
            {result.error_rows && result.error_rows.length > 0 ? (
              <ul className="mt-2 text-xs text-warning space-y-0.5 max-h-32 overflow-y-auto">
                {result.error_rows.slice(0, 10).map((r) => (
                  <li key={r.row}>
                    row {r.row}: {r.reason}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}

        <div className="flex items-center justify-end gap-2 pt-2 border-t border-border">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={!file || submitting}
            onClick={submit}
          >
            {submitting ? "Working…" : dryRun ? "Run dry-run" : "Import"}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
