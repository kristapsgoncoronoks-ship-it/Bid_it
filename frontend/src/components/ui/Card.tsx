import type { ReactNode } from "react";

/**
 * Surface container. `title`/`actions` render an optional header row; `padded`
 * controls inner padding (turn off for edge-to-edge tables).
 */
export function Card({ title, actions, padded = true, className = "", children }: {
  title?: ReactNode;
  actions?: ReactNode;
  padded?: boolean;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={`rounded-xl border border-slate-200 bg-white shadow-xs ${className}`}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-3">
          {title && <h2 className="text-sm font-semibold text-slate-600">{title}</h2>}
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={padded ? "p-5" : ""}>{children}</div>
    </section>
  );
}

// A compact metric tile (label · big value · sub). Consolidates the KPI cards
// copy-pasted across Dashboard / reports.
const ACCENTS = {
  brand: "text-brand-600", emerald: "text-emerald-600", rose: "text-rose-600",
  amber: "text-amber-600", slate: "text-slate-800",
} as const;

export function StatCard({ label, value, sub, accent = "slate" }: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  accent?: keyof typeof ACCENTS;
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-xs">
      <div className="text-sm font-medium text-slate-500">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tracking-tight tabular-nums ${ACCENTS[accent]}`}>{value}</div>
      {sub && <div className="mt-1 text-xs text-slate-400">{sub}</div>}
    </div>
  );
}
