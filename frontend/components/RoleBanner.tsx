interface RoleBannerProps {
  role: string;
  text: string;
}

const ROLE_COLORS: Record<string, string> = {
  model_owner: "bg-sky-950 text-sky-200",
  validator: "bg-emerald-950 text-emerald-200",
  mrm_head: "bg-purple-950 text-purple-200",
  auditor: "bg-slate-800 text-slate-300",
};

export function RoleBanner({ role, text }: RoleBannerProps) {
  const colorClass = ROLE_COLORS[role] || "bg-slate-800 text-slate-300";
  return (
    <div className={`px-6 py-3 text-center text-sm ${colorClass}`}>{text}</div>
  );
}
