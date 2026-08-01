"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { api, ApiError, type GovernedModel, type ValidationRunSummary } from "@/lib/api";
import { TierBadge } from "@/components/TierBadge";
import Link from "next/link";

const RUN_STATUS_LABEL: Record<string, string> = {
  running: "Running…",
  awaiting_review: "Awaiting review",
  signed_off: "Signed off",
};

export default function ModelDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { token, role } = useAuth();

  const [model, setModel] = useState<GovernedModel | null>(null);
  const [runs, setRuns] = useState<ValidationRunSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  const canTrigger = role === "validator" || role === "mrm_head";

  const refresh = () => {
    if (!token) return;
    Promise.all([api.getModel(token, id), api.listValidationRunsForModel(token, id)])
      .then(([m, r]) => {
        setModel(m);
        setRuns(r);
      })
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? JSON.stringify(err.detail) : String(err));
      });
  };

  useEffect(refresh, [token, id]);

  const handleStart = async () => {
    if (!token) return;
    setStarting(true);
    setError(null);
    try {
      const result = await api.startValidationRun(token, id);
      router.push(`/dashboard/runs/${result.validation_run_id}`);
    } catch (err) {
      // Surfacing the backend's own message rather than a generic one —
      // it already explains segregation-of-duties denials clearly (e.g.
      // "you cannot validate a model you own"), which is more useful to
      // the person clicking than a paraphrase would be.
      setError(err instanceof ApiError ? JSON.stringify(err.detail) : String(err));
      setStarting(false);
    }
  };

  if (error) {
    return (
      <div className="rounded-lg border border-red-900 bg-red-950/50 p-4 text-sm text-red-300">
        {error}
      </div>
    );
  }

  if (!model) {
    return <div className="text-slate-500">Loading…</div>;
  }

  return (
    <div>
      <Link href="/dashboard" className="mb-4 inline-block text-sm text-slate-500 hover:text-slate-300">
        ← All models
      </Link>

      <div className="mb-6 flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-slate-50">{model.name}</h1>
          <p className="mt-1 max-w-2xl text-slate-400">{model.description}</p>
          <div className="mt-3 flex items-center gap-3">
            <TierBadge tier={model.materiality_tier} />
            <span className="text-sm text-slate-500">{model.owner_team}</span>
          </div>
        </div>

        {canTrigger ? (
          <button
            onClick={handleStart}
            disabled={starting}
            className="rounded-md bg-emerald-600 px-4 py-2 font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
          >
            {starting ? "Starting…" : "Start Validation Run"}
          </button>
        ) : (
          <div className="rounded-md bg-slate-800 px-4 py-2 text-sm text-slate-400">
            {role === "auditor"
              ? "Auditors have read-only access"
              : "Only validators and MRM heads can trigger runs"}
          </div>
        )}
      </div>

      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
        Validation runs
      </h2>

      {runs && runs.length === 0 && (
        <p className="text-slate-500">No validation runs yet for this model.</p>
      )}

      {runs && runs.length > 0 && (
        <div className="space-y-2">
          {runs.map((run) => (
            <Link
              key={run.id}
              href={`/dashboard/runs/${run.id}`}
              className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900/40 p-4 hover:bg-slate-900"
            >
              <div>
                <span className="text-sm text-slate-300">
                  {RUN_STATUS_LABEL[run.status] || run.status}
                </span>
                <span className="ml-3 text-xs text-slate-600">
                  started {new Date(run.created_at).toLocaleString()}
                </span>
              </div>
              <span className="text-slate-600">→</span>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
