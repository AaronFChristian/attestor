const TIER_STYLES: Record<string, { label: string; className: string }> = {
  tier_1: { label: "Tier 1 — Full sweep", className: "bg-red-950 text-red-300 border-red-800" },
  tier_2: { label: "Tier 2 — Partial", className: "bg-amber-950 text-amber-300 border-amber-800" },
  tier_3: { label: "Tier 3 — Monitoring", className: "bg-emerald-950 text-emerald-300 border-emerald-800" },
};

export function TierBadge({ tier }: { tier: string }) {
  const style = TIER_STYLES[tier] || {
    label: tier,
    className: "bg-slate-800 text-slate-300 border-slate-700",
  };
  return (
    <span
      className={`inline-block rounded-full border px-2.5 py-0.5 text-xs font-medium ${style.className}`}
    >
      {style.label}
    </span>
  );
}
