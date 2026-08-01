interface PersonaCardProps {
  role: string;
  job: string;
  cannot: string;
}

export function PersonaCard({ role, job, cannot }: PersonaCardProps) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-5">
      <h3 className="mb-2 font-semibold text-slate-100">{role}</h3>
      <p className="mb-3 text-sm text-slate-300">{job}</p>
      <p className="text-xs text-slate-500">
        <span className="font-medium text-slate-400">Deliberately can&rsquo;t:</span>{" "}
        {cannot}
      </p>
    </div>
  );
}
