"use client";

import { useState } from "react";
import Link from "next/link";
import {
  Zap,
  Search,
  Sparkles,
  Send,
  Inbox,
  TrendingUp,
  CheckCircle2,
  ArrowRight,
} from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

const FEATURES = [
  {
    icon: Search,
    title: "Finds your clients",
    text: "Describe your ideal client once. Disha researches the web weekly and surfaces real decision-makers — with verified emails and the reason they fit you.",
  },
  {
    icon: Sparkles,
    title: "Researches every lead",
    text: "Company website, funding triggers, why-now timing, and a personal opening hook — generated before you ever hit send.",
  },
  {
    icon: Send,
    title: "Sends like a human",
    text: "Personalised multi-touch sequences from your own Gmail, ramped slowly to protect your sender reputation. You approve everything.",
  },
  {
    icon: Inbox,
    title: "Converts replies to calls",
    text: "Replies are classified by AI, you get notified instantly, and a drafted response with your booking link is one click away.",
  },
  {
    icon: TrendingUp,
    title: "Improves itself weekly",
    text: "Every Sunday it analyses what worked — which segments replied, which messages landed — and retargets next week's sourcing.",
  },
];

export default function WelcomePage() {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const [stage, setStage] = useState("");
  const [acquisition, setAcquisition] = useState("");
  const [pipelineGoal, setPipelineGoal] = useState("");
  const [challenge, setChallenge] = useState("");
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function join(e: React.FormEvent) {
    e.preventDefault();
    if (!email || busy) return;
    setBusy(true);
    setError("");
    try {
      const res = await fetch(`${API_BASE}/api/v1/public/waitlist`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email,
          name,
          note,
          stage,
          acquisition,
          pipeline_goal: pipelineGoal,
          challenge,
          source: "landing",
        }),
      });
      if (!res.ok) throw new Error("Something went wrong — try again");
      setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Try again");
    } finally {
      setBusy(false);
    }
  }

  const selectCls =
    "w-full px-3 py-2.5 bg-cream border border-border-strong rounded-md text-sm text-ink " +
    "focus:outline-none focus:border-terracotta";

  return (
    <div className="min-h-screen bg-cream relative overflow-hidden">
      <div className="relative max-w-5xl mx-auto px-6">
        {/* Nav */}
        <nav className="flex items-center justify-between py-6">
          <div className="flex items-center gap-2.5">
            <div className="w-9 h-9 rounded-xl bg-accent-gradient flex items-center justify-center shadow-accent">
              <Zap size={16} className="text-white" fill="currentColor" />
            </div>
            <span className="text-[17px] font-bold tracking-tight">Disha</span>
          </div>
          <Link
            href="/login"
            className="text-sm font-medium text-muted hover:text-ink transition-colors"
          >
            Member sign in →
          </Link>
        </nav>

        {/* Hero */}
        <div className="text-center pt-16 pb-12 max-w-3xl mx-auto">
          <div className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full bg-terracotta-wash text-terracotta text-xs font-bold mb-6">
            <Sparkles size={13} />
            Invite-only beta for independent consultants
          </div>
          <h1 className="text-5xl sm:text-6xl font-bold tracking-tight text-ink leading-[1.05]">
            Your consulting pipeline,
            <br />
            <span className="text-gradient">on autopilot.</span>
          </h1>
          <p className="text-lg text-muted mt-6 leading-relaxed max-w-2xl mx-auto">
            Disha finds your ideal clients, researches why they need you,
            writes the outreach, sends it from your Gmail, and turns replies
            into booked calls — while you do the actual consulting.
          </p>

          {/* Waitlist form */}
          <div className="mt-10 max-w-md mx-auto">
            {done ? (
              <div className="bg-paper border border-green/30 rounded-2xl p-6 shadow-soft">
                <CheckCircle2 size={28} className="text-green mx-auto mb-2" />
                <div className="font-semibold text-ink">You&apos;re on the list!</div>
                <p className="text-sm text-muted mt-1">
                  We onboard a few consultants each week — you&apos;ll hear from
                  us at {email}.
                </p>
              </div>
            ) : (
              <form
                onSubmit={join}
                className="bg-paper border border-border rounded-2xl p-5 shadow-soft space-y-3 text-left"
              >
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Your name"
                  className="w-full px-4 py-2.5 bg-cream border border-border-strong rounded-lg text-sm
                    placeholder:text-muted/50 focus:outline-none focus:border-terracotta focus:ring-4 focus:ring-terracotta-wash"
                />
                <input
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                  className="w-full px-4 py-2.5 bg-cream border border-border-strong rounded-lg text-sm
                    placeholder:text-muted/50 focus:outline-none focus:border-terracotta focus:ring-4 focus:ring-terracotta-wash"
                />
                <textarea
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder="What do you consult on? (optional — helps us prioritise)"
                  rows={2}
                  className="w-full px-4 py-2.5 bg-cream border border-border-strong rounded-lg text-sm resize-none
                    placeholder:text-muted/50 focus:outline-none focus:border-terracotta focus:ring-4 focus:ring-terracotta-wash"
                />

                {/* Invite-tailoring questions (a.k.a. qualification) */}
                <div className="pt-1 space-y-3">
                  <p className="text-[11px] uppercase tracking-[0.06em] text-muted font-medium">
                    Help us tailor your invite
                  </p>
                  <select
                    value={stage}
                    onChange={(e) => setStage(e.target.value)}
                    className={selectCls}
                  >
                    <option value="">Where are you in consulting?</option>
                    <option value="exploring">Still exploring the idea</option>
                    <option value="starting">Just starting out</option>
                    <option value="few_clients">A few clients in</option>
                    <option value="established">Established practice</option>
                  </select>
                  <select
                    value={acquisition}
                    onChange={(e) => setAcquisition(e.target.value)}
                    className={selectCls}
                  >
                    <option value="">How do you find clients today?</option>
                    <option value="referrals">Referrals & network</option>
                    <option value="outbound">Cold outreach</option>
                    <option value="inbound">Content & inbound</option>
                    <option value="marketplaces">Marketplaces (Upwork etc.)</option>
                    <option value="none_yet">Not finding them yet</option>
                  </select>
                  <select
                    value={pipelineGoal}
                    onChange={(e) => setPipelineGoal(e.target.value)}
                    className={selectCls}
                  >
                    <option value="">Ideal new client conversations / month?</option>
                    <option value="1-2">1–2</option>
                    <option value="3-5">3–5</option>
                    <option value="6-10">6–10</option>
                    <option value="10+">10+</option>
                  </select>
                  <textarea
                    value={challenge}
                    onChange={(e) => setChallenge(e.target.value)}
                    placeholder="What's the hardest part of winning clients right now? (optional)"
                    rows={2}
                    className="w-full px-4 py-2.5 bg-cream border border-border-strong rounded-md text-sm resize-none
                      placeholder:text-muted/50 focus:outline-none focus:border-terracotta"
                  />
                </div>

                {error && <p className="text-xs text-red-500">{error}</p>}
                <button
                  type="submit"
                  disabled={busy || !email}
                  className="w-full py-3 bg-terracotta text-white text-sm font-semibold rounded-lg shadow-accent
                    hover:bg-terracotta/90 transition-all
                    disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center justify-center gap-2"
                >
                  {busy ? "Joining…" : "Request an invite"}
                  {!busy && <ArrowRight size={15} />}
                </button>
                <p className="text-[11px] text-muted/70 text-center">
                  No spam. We&apos;ll only email you about your invite.
                </p>
              </form>
            )}
          </div>
        </div>

        {/* Features */}
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4 pb-16 pt-4">
          {FEATURES.map((f) => (
            <div
              key={f.title}
              className="bg-paper border border-border rounded-xl p-5 shadow-card transition-all"
            >
              <div className="w-9 h-9 rounded-lg bg-terracotta-wash flex items-center justify-center mb-3">
                <f.icon size={16} className="text-terracotta" />
              </div>
              <div className="font-semibold text-ink text-[15px]">{f.title}</div>
              <p className="text-sm text-muted mt-1.5 leading-relaxed">{f.text}</p>
            </div>
          ))}
          {/* Filler card with the pitch */}
          <div className="bg-accent-gradient rounded-xl p-5 shadow-accent text-white flex flex-col justify-center">
            <div className="font-bold text-lg leading-snug">
              Built by a consultant, for consultants.
            </div>
            <p className="text-sm text-white/85 mt-2 leading-relaxed">
              You bring the expertise and your own accounts (Gmail, AI keys).
              Disha brings the machine. Your data stays yours.
            </p>
          </div>
        </div>

        <footer className="border-t border-border py-8 text-center text-xs text-muted/70">
          Disha — outbound console for independent consultants ·{" "}
          <Link href="/login" className="hover:text-ink">
            Sign in
          </Link>
        </footer>
      </div>
    </div>
  );
}
