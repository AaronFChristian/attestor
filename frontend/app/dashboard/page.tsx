"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { api, type GovernedModel, ApiError } from "@/lib/api";
import { TierBadge } from "@/components/TierBadge";

export default function DashboardHomePage() {
  const { token, role } = useAuth();
  const router = useRouter();
  const [models, setModels] = useState<GovernedModel[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    api
      .listModels(token)
      .then(setModels)
      .catch((err: unknown) => {
        if (err instanceof ApiError) {
          setError(`API error ${err.status}: ${JSON.stringify(err.detail)}`);
        } else {
          setError(String(err));
        }
      });
  }, [token]);

  return (
    <div>
      <div className="mb-6 rounded-lg border border-slate-800 bg-slate-900/40 p-4 text-sm text-slate-400">
        <span className="font-medium text-slate-300">What you&rsquo;re looking at:</span>{" "}
        every AI/ML system currently under Attestor&rsquo;s governance, with its
        materiality tier — a deterministic score, not an LLM guess (see the
        scorecard in <code className="rounded bg-slate-800 px-1">app/services/materiality.py</code>)
        — determining how much validation scrutiny it requires.
        {(role === "validator" || role === "mrm_head") && (
          <> Click a model to view or start a validation run.</>
        )}
      </div>

      {error && (
        <div className="rounded-lg border border-red-900 bg-red-950/50 p-4 text-sm text-red-300">
          {error}
        </div>
      )}

      {!models && !error && (
        <div className="text-slate-500">Loading governed models…</div>
      )}

      {models && models.length === 0 && (
        <div className="text-slate-500">
          No models registered yet. Run{" "}
          <code className="rounded bg-slate-800 px-1">scripts/seed_data.py</code>{" "}
          to seed the demo inventory.
        </div>
      )}

      {models && models.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-slate-800">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-900 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-3">Model</th>
                <th className="px-4 py-3">Owner team</th>
                <th className="px-4 py-3">Type</th>
                <th className="px-4 py-3">Materiality</th>
                <th className="px-4 py-3">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800">
              {models.map((m) => (
                <tr
                  key={m.id}
                  onClick={() => router.push(`/dashboard/models/${m.id}`)}
                  className="cursor-pointer hover:bg-slate-900/50"
                >
                  <td className="px-4 py-3">
                    <div className="font-medium text-slate-100">
                      {m.name}
                      {m.is_self_governance && (
                        <span
                          className="ml-2 rounded bg-purple-950 px-1.5 py-0.5 text-xs text-purple-300"
                          title="Attestor governing its own validation agent under this same framework"
                        >
                          self-governance
                        </span>
                      )}
                    </div>
                    <div className="text-xs text-slate-500">{m.description}</div>
                  </td>
                  <td className="px-4 py-3 text-slate-400">{m.owner_team}</td>
                  <td className="px-4 py-3 text-slate-400">{m.model_type}</td>
                  <td className="px-4 py-3">
                    <TierBadge tier={m.materiality_tier} />
                    <div className="mt-1 text-xs text-slate-500">
                      {m.materiality_score?.rationale}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-slate-400">{m.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
