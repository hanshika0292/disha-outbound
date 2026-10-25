"use client";

import { useState } from "react";
import { Dialog } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input, Label, Textarea } from "@/components/ui/inputs";
import { useToast } from "@/components/ui/toast";
import { apiFetch } from "@/lib/api";

type AddLeadResult = { lead_id: string | null };

export function AddLeadModal({
  open,
  onOpenChange,
  onSuccess,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onSuccess: () => void;
}) {
  const [form, setForm] = useState({
    first_name: "",
    last_name: "",
    email: "",
    title: "",
    company: "",
    country: "",
    linkedin_url: "",
    notes: "",
  });
  const [autoScore, setAutoScore] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const toast = useToast();

  function update<K extends keyof typeof form>(k: K, v: string) {
    setForm((f) => ({ ...f, [k]: v }));
  }
  function reset() {
    setForm({
      first_name: "",
      last_name: "",
      email: "",
      title: "",
      company: "",
      country: "",
      linkedin_url: "",
      notes: "",
    });
  }

  async function submit() {
    setSubmitting(true);
    try {
      const res = await apiFetch<AddLeadResult>("/api/v1/leads", {
        method: "POST",
        body: form,
        params: { auto_score: autoScore },
      });
      if (!res.lead_id) {
        toast.push({ title: "Already exists", tone: "neutral" });
      } else {
        toast.push({ title: "Lead added", tone: "success" });
        onSuccess();
        onOpenChange(false);
        reset();
      }
    } catch (e) {
      toast.push({
        title: "Add failed",
        description: e instanceof Error ? e.message : String(e),
        tone: "error",
      });
    } finally {
      setSubmitting(false);
    }
  }

  const required = ["first_name", "last_name", "email", "title", "company", "country"] as const;
  const valid = required.every((k) => form[k].trim().length > 0);

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        onOpenChange(o);
        if (!o) reset();
      }}
      title="Add a lead"
      description="Required fields are marked. Duplicate emails are rejected."
      size="lg"
    >
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label>First name *</Label>
            <Input
              value={form.first_name}
              onChange={(e) => update("first_name", e.target.value)}
            />
          </div>
          <div>
            <Label>Last name *</Label>
            <Input
              value={form.last_name}
              onChange={(e) => update("last_name", e.target.value)}
            />
          </div>
        </div>
        <div>
          <Label>Email *</Label>
          <Input
            type="email"
            value={form.email}
            onChange={(e) => update("email", e.target.value)}
          />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label>Title *</Label>
            <Input
              value={form.title}
              onChange={(e) => update("title", e.target.value)}
            />
          </div>
          <div>
            <Label>Company *</Label>
            <Input
              value={form.company}
              onChange={(e) => update("company", e.target.value)}
            />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label>Country *</Label>
            <Input
              value={form.country}
              onChange={(e) => update("country", e.target.value)}
              placeholder="IN, US, GB…"
            />
          </div>
          <div>
            <Label>LinkedIn URL</Label>
            <Input
              value={form.linkedin_url}
              onChange={(e) => update("linkedin_url", e.target.value)}
              placeholder="https://linkedin.com/in/…"
            />
          </div>
        </div>
        <div>
          <Label>Notes</Label>
          <Textarea
            value={form.notes}
            onChange={(e) => update("notes", e.target.value)}
            placeholder="Anything to remember about this person…"
          />
        </div>

        <label className="flex items-center gap-2 text-sm pt-1">
          <input
            type="checkbox"
            checked={autoScore}
            onChange={(e) => setAutoScore(e.target.checked)}
            className="accent-terracotta"
          />
          <span>Auto-score immediately</span>
        </label>

        <div className="flex items-center justify-end gap-2 pt-3 border-t border-border">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!valid || submitting} onClick={submit}>
            {submitting ? "Saving…" : "Add lead"}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
