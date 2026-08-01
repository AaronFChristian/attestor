"use client";

import { useAuth } from "@/lib/auth-context";
import { ArchitectureDiagram } from "@/components/ArchitectureDiagram";
import { PersonaCard } from "@/components/PersonaCard";
import Link from "next/link";

export default function LandingPage() {
  const { initialized, authenticated, login, name, role } = useAuth();

  return (
    <main className="mx-auto max-w-5xl px-6 py-16">
      {/* Top bar: quiet auth state, not the main event */}
      <div className="mb-16 flex items-center justify-between text-sm text-slate-400">
        <span className="font-medium tracking-wide text-slate-200">ATTESTOR</span>
        {!initialized ? (
          <span>checking session…</span>
        ) : authenticated ? (
          <Link
            href="/dashboard"
            className="rounded-md bg-slate-800 px-4 py-2 text-slate-100 hover:bg-slate-700"
          >
            Continue as {name} ({role}) →
          </Link>
        ) : (
          <button
            onClick={login}
            className="rounded-md bg-emerald-600 px-4 py-2 font-medium text-white hover:bg-emerald-500"
          >
            Sign in
          </button>
        )}
      </div>

      {/* The pitch — this is what a cold viewer reads first */}
      <section className="mb-20">
        <h1 className="mb-4 text-4xl font-semibold leading-tight text-slate-50">
          A validation report is only as good as
          <br />
          <span className="text-emerald-400">what it can prove.</span>
        </h1>
        <p className="max-w-2xl text-lg leading-relaxed text-slate-300">
          Under the new SR 26-2 model-risk regime, every GenAI and agentic system
          a bank runs needs conceptual soundness review, outcomes analysis, and
          ongoing monitoring — with a validation report that examiners can
          defend. Attestor is a governance platform where an AI validation
          agent proposes findings, but{" "}
          <strong className="text-slate-100">
            every single finding is checked against real evidence before it&rsquo;s
            ever shown to a human
          </strong>
          . A finding with no matching evidence record simply never gets
          persisted — not filtered out later, not softened, blocked at write
          time.
        </p>
      </section>

      {/* The concrete failure mode this solves */}
      <section className="mb-20 rounded-xl border border-slate-800 bg-slate-900/50 p-6">
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-amber-400">
          The problem this solves
        </h2>
        <p className="text-slate-300">
          LLM-as-judge systems hallucinate. A validation agent that says
          &ldquo;faithfulness collapsed to 0.42&rdquo; is dangerous precisely
          because it sounds credible — the number is specific, the claim is
          plausible, and nobody manually re-derives it before it lands in a
          report an examiner reads. Attestor&rsquo;s attribution gate makes that
          exact failure structurally impossible: every cited metric is
          compared against the actual stored eval run before the finding is
          written. Try it in the review workspace and you&rsquo;ll see findings
          that survived that check next to ones that didn&rsquo;t.
        </p>
      </section>

      {/* Architecture at a glance */}
      <section className="mb-20">
        <h2 className="mb-6 text-sm font-semibold uppercase tracking-wide text-slate-400">
          How a validation actually runs
        </h2>
        <ArchitectureDiagram />
      </section>

      {/* Who uses this, and what they'd click */}
      <section className="mb-20">
        <h2 className="mb-6 text-sm font-semibold uppercase tracking-wide text-slate-400">
          Four roles, four different jobs
        </h2>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <PersonaCard
            role="Model Owner"
            job="Registers models and responds to findings raised against them."
            cannot="Cannot validate a model they own, and cannot close a finding — segregation of duties, enforced server-side, not just in the UI."
          />
          <PersonaCard
            role="Validator"
            job="Triggers validation runs and works the review queue — accept, reject, or amend each AI-proposed finding with a written rationale."
            cannot="Cannot sign off their own report. A validator's review and an mrm_head's attestation are deliberately two different people."
          />
          <PersonaCard
            role="MRM Head"
            job="Signs off on finalized reports — the human accountability anchor the whole system exists to produce."
            cannot="Cannot review individual findings on a run they didn't oversee end-to-end without the signal being visible in the audit trail."
          />
          <PersonaCard
            role="Auditor"
            job="Read-only access to everything — every model, every finding, every signed-off report, the full hash-chained audit log."
            cannot="Cannot write anything, anywhere, ever. That's not a limitation, it's the point."
          />
        </div>
      </section>

      <div className="border-t border-slate-800 pt-8 text-center">
        {!authenticated && initialized && (
          <button
            onClick={login}
            className="rounded-md bg-emerald-600 px-6 py-3 font-medium text-white hover:bg-emerald-500"
          >
            Sign in to try it →
          </button>
        )}
      </div>
    </main>
  );
}
