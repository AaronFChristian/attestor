"use client";

import { useState } from "react";
import type { Finding } from "@/lib/api";

const SEVERITY_STYLES: Record<string, string> = {
  low: "border-slate-700 bg-slate-800/50 text-slate-300",
  medium: "border-amber-800 bg-amber-950/50 text-amber-300",
  high: "border-orange-800 bg-orange-950/50 text-orange-300",
  critical: "border-red-800 bg-red-950/50 text-red-300",
};

const STATUS_STYLES: Record<string, string> = {
  proposed: "bg-amber-900 text-amber-200",
  accepted: "bg-emerald-900 text-emerald-200",
  amended: "bg-sky-900 text-sky-200",
  rejected: "bg-slate-700 text-slate-300",
};

interface FindingReviewCardProps {
  finding: Finding;
  canReview: boolean;
  onReview: (
    action: "accept" | "reject" | "amend",
    rationale: string,
    amendedClaim?: string
  ) => Promise<void>;
}

export function FindingReviewCard({ finding, canReview, onReview }: FindingReviewCardProps) {
  const [mode, setMode] = useState<"idle" | "accept" | "reject" | "amend">("idle");
  const [rationale, setRationale] = useState("");
  const [amendedClaim, setAmendedClaim] = useState(finding.claim);
  const [submitting, setSubmitting] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  const isReviewed = finding.status !== "proposed";

  const submit = async (action: "accept" | "reject" | "amend") => {
    if (!rationale.trim()) {
      setLocalError("A rationale is required — this is what makes the review meaningful.");
      return;
    }
    setSubmitting(true);
    setLocalError(null);
    try {
      await onReview(action, rationale, action === "amend" ? amendedClaim : undefined);
      setMode("idle");
    } catch (err) {
      setLocalError(String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className={`rounded-lg border p-4 ${SEVERITY_STYLES[finding.severity] || "border-slate-700 bg-slate-800/50"}`}
    >
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="rounded bg-slate-800 px-2 py-0.5 text-xs font-medium uppercase tracking-wide text-slate-400">
            {finding.pillar.replace("_", " ")}
          </span>
          <span className="text-xs font-medium uppercase">{finding.severity}</span>
        </div>
        <span
          className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${STATUS_STYLES[finding.status]}`}
        >
          {finding.status}
        </span>
      </div>

      <p className="mb-2 text-sm leading-relaxed text-slate-200">{finding.claim}</p>

      <p className="mb-3 text-xs text-slate-500">
        raised by <code className="rounded bg-slate-800 px-1">{finding.raised_by}</code> · evidence{" "}
        <code className="rounded bg-slate-800 px-1">{finding.evidence_id.slice(0, 8)}…</code>{" "}
        <span className="italic">
          — this is what the attribution gate checked before this finding was ever
          allowed to be written
        </span>
      </p>

      {!canReview && !isReviewed && (
        <p className="text-xs text-slate-500">Only a validator or MRM head can review this.</p>
      )}

      {canReview && !isReviewed && mode === "idle" && (
        <div className="flex gap-2">
          <button
            onClick={() => setMode("accept")}
            className="rounded-md bg-emerald-700 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-600"
          >
            Accept
          </button>
          <button
            onClick={() => setMode("reject")}
            className="rounded-md bg-slate-700 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-600"
          >
            Reject
          </button>
          <button
            onClick={() => setMode("amend")}
            className="rounded-md bg-sky-700 px-3 py-1.5 text-xs font-medium text-white hover:bg-sky-600"
          >
            Amend
          </button>
        </div>
      )}

      {mode !== "idle" && (
        <div className="mt-2 space-y-2 rounded-md bg-slate-950/50 p-3">
          {mode === "amend" && (
            <textarea
              value={amendedClaim}
              onChange={(e) => setAmendedClaim(e.target.value)}
              className="w-full rounded-md border border-slate-700 bg-slate-900 p-2 text-sm text-slate-200"
              rows={2}
            />
          )}
          <textarea
            value={rationale}
            onChange={(e) => setRationale(e.target.value)}
            placeholder={
              mode === "reject"
                ? "Why is this finding being rejected? (feeds Attestor's own golden set)"
                : "Rationale — required for every review action"
            }
            className="w-full rounded-md border border-slate-700 bg-slate-900 p-2 text-sm text-slate-200"
            rows={2}
          />
          {localError && <p className="text-xs text-red-400">{localError}</p>}
          <div className="flex gap-2">
            <button
              onClick={() => submit(mode)}
              disabled={submitting}
              className="rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
            >
              {submitting ? "Submitting…" : `Confirm ${mode}`}
            </button>
            <button
              onClick={() => setMode("idle")}
              className="rounded-md bg-slate-800 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-700"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {isReviewed && finding.status === "rejected" && (
        <p className="mt-2 text-xs text-slate-500 italic">
          Rejected findings feed Attestor&rsquo;s own golden set — this is what makes the
          validation agent&rsquo;s false-finding rate measurable rather than assumed.
        </p>
      )}
    </div>
  );
}
