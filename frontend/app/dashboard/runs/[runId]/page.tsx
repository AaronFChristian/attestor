"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { api, ApiError, type Finding, type ValidationRunDetail } from "@/lib/api";
import { PipelineVisualizer } from "@/components/PipelineVisualizer";
import { FindingReviewCard } from "@/components/FindingReviewCard";
import Link from "next/link";

export default function ValidationRunPage() {
  const { runId } = useParams<{ runId: string }>();
  const { token, role, email } = useAuth();

  const [run, setRun] = useState<ValidationRunDetail | null>(null);
  const [findings, setFindings] = useState<Finding[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const canReview = role === "validator" || role === "mrm_head";
  const canSignOff = role === "mrm_head";

  const refresh = () => {
    if (!token) return;
    Promise.all([api.getValidationRun(token, runId), api.listFindings(token, runId)])
      .then(([r, f]) => {
        setRun(r);
        setFindings(f);
      })
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? JSON.stringify(err.detail) : String(err));
      });
  };

  useEffect(refresh, [token, runId]);

  const handleReview = async (
    findingId: string,
    action: "accept" | "reject" | "amend",
    rationale: string,
    amendedClaim?: string
  ) => {
    if (!token) return;
    await api.reviewFinding(token, runId, findingId, action, rationale, amendedClaim);
    refresh();
  };

  const handleFinalize = async () => {
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      await api.finalizeRun(token, runId);
      refresh();
    } catch (err) {
      setError(err instanceof ApiError ? JSON.stringify(err.detail) : String(err));
    } finally {
      setBusy(false);
    }
  };

  const handleSignOff = async () => {
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      await api.signOffRun(token, runId);
      refresh();
    } catch (err) {
      setError(err instanceof ApiError ? JSON.stringify(err.detail) : String(err));
    } finally {
      setBusy(false);
    }
  };

  if (!run || !findings) {
    return <div className="text-slate-500">{error || "Loading…"}</div>;
  }

  const allReviewed = findings.every((f) => f.status !== "proposed");
  const canFinalize = run.status === "awaiting_review" && !run.report_evidence_id && allReviewed;
  const canSignOffNow = run.status === "awaiting_review" && !!run.report_evidence_id;

  return (
    <div>
      <Link
        href={`/dashboard/models/${run.model_id}`}
        className="mb-4 inline-block text-sm text-slate-500 hover:text-slate-300"
      >
        ← Back to model
      </Link>

      <h1 className="mb-1 text-2xl font-semibold text-slate-50">Validation Run</h1>
      <p className="mb-6 text-sm text-slate-500">
        Status: <span className="text-slate-300">{run.status}</span>
        {run.status === "signed_off" && (
          <span className="ml-2 text-emerald-400">✓ signed off {run.signed_off_at}</span>
        )}
      </p>

      <div className="mb-8">
        <PipelineVisualizer run={run} />
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-red-900 bg-red-950/50 p-4 text-sm text-red-300">
          {error}
        </div>
      )}

      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
        Findings ({findings.length})
      </h2>

      {findings.length === 0 && (
        <p className="text-slate-500">
          No findings survived the attribution gate for this run — either the model had
          nothing wrong the checked pillars could ground a claim in, or every proposed
          finding was rejected at write time.
        </p>
      )}

      <div className="space-y-3">
        {findings.map((f) => (
          <FindingReviewCard
            key={f.id}
            finding={f}
            canReview={canReview}
            onReview={(action, rationale, amendedClaim) =>
              handleReview(f.id, action, rationale, amendedClaim)
            }
          />
        ))}
      </div>

      <div className="mt-8 flex gap-3 border-t border-slate-800 pt-6">
        {canReview && canFinalize && (
          <button
            onClick={handleFinalize}
            disabled={busy}
            className="rounded-md bg-emerald-600 px-4 py-2 font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
          >
            {busy ? "Finalizing…" : "Finalize Report"}
          </button>
        )}
        {canReview && run.status === "awaiting_review" && !run.report_evidence_id && !allReviewed && (
          <p className="text-sm text-slate-500">
            Review every finding before finalizing ({findings.filter((f) => f.status === "proposed").length} remaining).
          </p>
        )}

        {canSignOffNow && canSignOff && (
          <button
            onClick={handleSignOff}
            disabled={busy}
            className="rounded-md bg-purple-600 px-4 py-2 font-medium text-white hover:bg-purple-500 disabled:opacity-50"
          >
            {busy ? "Signing off…" : `Sign Off as ${email}`}
          </button>
        )}
        {canSignOffNow && !canSignOff && (
          <p className="text-sm text-slate-500">
            Report is finalized — only an MRM Head can sign off from here.
          </p>
        )}
      </div>
    </div>
  );
}
