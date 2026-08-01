import type { ValidationRunDetail } from "@/lib/api";

/**
 * Mirrors ArchitectureDiagram's step list, but this one is LIVE — it
 * highlights the step that matches the run's actual current state rather
 * than just describing the pipeline in the abstract. Derivation logic:
 *
 * - status === "running" -> somewhere in supervisor/pillars/challenge/gate
 *   (we can't distinguish further without polling mid-graph state, which
 *   isn't exposed — see the honest note in the caption)
 * - status === "awaiting_review" and report_evidence_id is null -> PAUSED,
 *   waiting on findings review
 * - status === "awaiting_review" and report_evidence_id is set -> report
 *   drafted, waiting on sign-off
 * - status === "signed_off" -> done
 */
const STAGES = [
  "Supervisor",
  "Pillar nodes",
  "Challenge",
  "Attribution gate",
  "⏸ Paused",
  "Human review",
  "Finalize report",
  "Sign-off",
] as const;

function currentStageIndex(run: ValidationRunDetail): number {
  if (run.status === "signed_off") return 7;
  if (run.status === "awaiting_review" && run.report_evidence_id) return 7; // report done, waiting sign-off
  if (run.status === "awaiting_review") return 5; // paused, findings exist, waiting review
  return 1; // "running" — somewhere in the fast synchronous portion
}

export function PipelineVisualizer({ run }: { run: ValidationRunDetail }) {
  const activeIndex = currentStageIndex(run);

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/30 p-6">
      <div className="flex min-w-max items-stretch gap-2 overflow-x-auto">
        {STAGES.map((stage, i) => {
          const isActive = i === activeIndex;
          const isPast = i < activeIndex;
          return (
            <div key={stage} className="flex items-stretch gap-2">
              <div
                className={`flex w-32 items-center justify-center rounded-lg p-3 text-center text-xs font-medium transition-colors ${
                  isActive
                    ? "bg-amber-600 text-white ring-2 ring-amber-400"
                    : isPast
                      ? "bg-emerald-900 text-emerald-300"
                      : "bg-slate-800 text-slate-500"
                }`}
              >
                {stage}
              </div>
              {i < STAGES.length - 1 && (
                <div className="flex items-center text-slate-700">→</div>
              )}
            </div>
          );
        })}
      </div>
      <p className="mt-4 text-xs text-slate-500">
        <span className="font-medium text-slate-400">Honest limitation:</span>{" "}
        this run has no push/websocket layer, so the exact node executing
        mid-graph (supervisor vs. a specific pillar vs. challenge) isn&rsquo;t
        individually observable from here — only the state before and after
        the pause is. The pause point itself, however, is a real LangGraph
        checkpoint, not a UI approximation.
      </p>
    </div>
  );
}
