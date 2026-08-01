/**
 * Thin fetch wrapper. Not a heavy client library on purpose — the backend
 * surface is small enough (Days 1-3) that a generated SDK would be more
 * ceremony than value right now. Every call requires a token explicitly
 * passed in, rather than reading it from a global — keeps this file
 * decoupled from the auth context, easier to reason about, easier to test.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(`API error ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(
  path: string,
  token: string,
  options: RequestInit = {}
): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      ...options.headers,
    },
  });

  if (!res.ok) {
    let detail: unknown;
    try {
      detail = await res.json();
    } catch {
      detail = await res.text();
    }
    throw new ApiError(res.status, detail);
  }

  // 202/204 responses may have no body.
  const text = await res.text();
  return text ? (JSON.parse(text) as T) : (undefined as T);
}

export interface GovernedModel {
  id: string;
  name: string;
  description: string;
  owner_team: string;
  materiality_tier: "tier_1" | "tier_2" | "tier_3";
  materiality_score: {
    weighted_score: number;
    rationale: string;
  };
  status: string;
  model_type: string;
  is_self_governance: boolean;
  created_at: string;
}

export interface Finding {
  id: string;
  pillar: string;
  claim: string;
  severity: "low" | "medium" | "high" | "critical";
  status: "proposed" | "accepted" | "amended" | "rejected" | "remediated";
  raised_by: string;
  evidence_id: string;
}

export interface ValidationRunSummary {
  id: string;
  status: string;
  created_at: string;
  signed_off_at: string | null;
}

export interface ValidationRunDetail {
  id: string;
  model_id: string;
  status: string;
  report_evidence_id: string | null;
  signed_off_by_user_id: string | null;
  signed_off_at: string | null;
  created_at: string;
  finding_counts: Record<string, number>;
  total_findings: number;
}

export const api = {
  listModels: (token: string) => request<GovernedModel[]>("/models", token),

  getModel: (token: string, id: string) =>
    request<GovernedModel>(`/models/${id}`, token),

  listValidationRunsForModel: (token: string, modelId: string) =>
    request<ValidationRunSummary[]>(`/models/${modelId}/validation-runs`, token),

  getValidationRun: (token: string, runId: string) =>
    request<ValidationRunDetail>(`/validation-runs/${runId}`, token),

  startValidationRun: (token: string, modelId: string) =>
    request<{ validation_run_id: string; status: string; note: string }>(
      "/validation-runs",
      token,
      { method: "POST", body: JSON.stringify({ model_id: modelId }) }
    ),

  listFindings: (token: string, runId: string) =>
    request<Finding[]>(`/validation-runs/${runId}/findings`, token),

  reviewFinding: (
    token: string,
    runId: string,
    findingId: string,
    action: "accept" | "reject" | "amend",
    rationale: string,
    amendedClaim?: string
  ) =>
    request<{ finding_id: string; status: string }>(
      `/validation-runs/${runId}/findings/${findingId}/${action}`,
      token,
      {
        method: "POST",
        body: JSON.stringify({ rationale, amended_claim: amendedClaim }),
      }
    ),

  finalizeRun: (token: string, runId: string) =>
    request<{ validation_run_id: string; report_evidence_id: string; note: string }>(
      `/validation-runs/${runId}/finalize`,
      token,
      { method: "POST" }
    ),

  signOffRun: (token: string, runId: string) =>
    request<{ validation_run_id: string; status: string; signed_off_by: string }>(
      `/validation-runs/${runId}/sign-off`,
      token,
      { method: "POST" }
    ),
};
