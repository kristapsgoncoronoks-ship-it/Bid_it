interface Props {
  label: string;
  value: string;
  sub?: string;
  accent?: "brand" | "emerald" | "rose" | "amber";
}

const ACCENTS: Record<string, string> = {
  brand: "text-brand-600",
  emerald: "text-emerald-600",
  rose: "text-rose-600",
  amber: "text-amber-600",
};

export function KpiCard({ label, value, sub, accent = "brand" }: Props) {
  return (
    <div className="card">
      <div className="text-sm font-medium text-slate-500">{label}</div>
      <div className={`mt-2 text-2xl font-semibold ${ACCENTS[accent]}`}>{value}</div>
      {sub && <div className="mt-1 text-xs text-slate-400">{sub}</div>}
    </div>
  );
}
