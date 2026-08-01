/**
 * This deliberately mirrors the REAL graph topology in
 * app/agents/validation_graph.py — supervisor fan-out, convergence on
 * challenge, the gate, the interrupt, then finalize + sign-off. If the
 * backend graph topology changes, this diagram goes stale and should be
 * updated alongside it, not treated as decorative.
 */
const steps = [
  {
    label: "Supervisor",
    detail: "Reads materiality tier, decides which pillars run",
    color: "bg-slate-700",
  },
  {
    label: "3 pillar nodes (parallel)",
    detail: "Conceptual soundness · Outcomes analysis · Ongoing monitoring",
    color: "bg-sky-800",
  },
  {
    label: "Challenge",
    detail: "Independent adversarial review of everything proposed",
    color: "bg-sky-800",
  },
  {
    label: "Attribution gate",
    detail: "Every finding checked against real evidence — no match, no write",
    color: "bg-red-800",
  },
  {
    label: "⏸ PAUSE",
    detail: "Graph interrupts here. Nothing below runs until a human resumes it.",
    color: "bg-amber-700",
  },
  {
    label: "Human review",
    detail: "Validator accepts / rejects / amends each finding, with rationale",
    color: "bg-emerald-800",
  },
  {
    label: "Finalize report",
    detail: "Re-reads the database (not old state) — reflects every human edit",
    color: "bg-slate-700",
  },
  {
    label: "Sign-off",
    detail: "MRM Head attests — never the same person who ran the review",
    color: "bg-emerald-800",
  },
];

export function ArchitectureDiagram() {
  return (
    <div className="overflow-x-auto rounded-xl border border-slate-800 bg-slate-900/30 p-6">
      <div className="flex min-w-max items-stretch gap-3">
        {steps.map((step, i) => (
          <div key={step.label} className="flex items-stretch gap-3">
            <div
              className={`flex w-44 flex-col justify-center rounded-lg ${step.color} p-3 text-white`}
            >
              <div className="text-sm font-semibold">{step.label}</div>
              <div className="mt-1 text-xs leading-snug text-white/80">
                {step.detail}
              </div>
            </div>
            {i < steps.length - 1 && (
              <div className="flex items-center text-slate-600">→</div>
            )}
          </div>
        ))}
      </div>
      <p className="mt-4 text-xs text-slate-500">
        The pause is not cosmetic — it&rsquo;s a real LangGraph{" "}
        <code className="rounded bg-slate-800 px-1">interrupt_before</code>{" "}
        checkpoint. Findings are already real, persisted, database rows by the
        time it happens; nothing about the report exists until a human
        resumes the graph.
      </p>
    </div>
  );
}
