import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { BudgetTrendChart } from "../components/Charts";
import { KpiCard } from "../components/KpiCard";
import { useToast } from "../components/Toast";
import { api, apiError } from "../lib/api";
import { money } from "../lib/format";
import type { BudgetOverview, BudgetRow } from "../lib/types";

function thisMonth(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export default function Budget() {
  const qc = useQueryClient();
  const toast = useToast();
  const [month, setMonth] = useState(thisMonth());
  const [newCat, setNewCat] = useState("");
  const [newLimit, setNewLimit] = useState("");

  const key = ["budget", month];
  const { data, isLoading } = useQuery<BudgetOverview>({
    queryKey: key,
    queryFn: async () => (await api.get(`/budget/overview?month=${month}`)).data,
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["budget"] });
  const setTarget = useMutation({
    mutationFn: async (v: { category: string; monthly_limit: string }) =>
      (await api.put("/budget/targets", v)).data,
    onSuccess: invalidate,
    onError: (e) => toast.error(apiError(e)),
  });
  const removeTarget = useMutation({
    mutationFn: async (category: string) => api.delete(`/budget/targets/${encodeURIComponent(category)}`),
    onSuccess: invalidate,
    onError: (e) => toast.error(apiError(e)),
  });

  const overAccent = data?.over_budget ? "rose" : "emerald";

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Monthly budget</h1>
          <p className="text-sm text-slate-500">
            Set a monthly limit per category and track it against the invoices you actually received.
            <span className="text-slate-400"> Actuals are VAT-inclusive, converted to EUR at the ECB rate.</span>
          </p>
        </div>
        <div>
          <label className="label">Month</label>
          <input type="month" className="input" value={month} onChange={(e) => setMonth(e.target.value)} />
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <KpiCard label="Budgeted" value={money(data?.total_budget ?? 0)} sub="sum of category limits" />
        <KpiCard label="Spent" value={money(data?.total_actual ?? 0)} accent="brand" sub={`received invoices · ${month}`} />
        <KpiCard
          label={data?.over_budget ? "Over budget" : "Remaining"}
          value={money(data?.total_remaining ?? 0)}
          accent={overAccent}
          sub={data?.over_budget ? "spend exceeds budget" : "left to spend"}
        />
      </div>

      <div className="card">
        <h2 className="mb-3 text-sm font-semibold text-slate-600">Actual vs budget · last 6 months</h2>
        {data && data.trend.length > 0 ? (
          <BudgetTrendChart data={data.trend} />
        ) : (
          <div className="py-10 text-center text-sm text-slate-400">No data yet.</div>
        )}
      </div>

      <div className="card space-y-4 p-0">
        <div className="border-b border-slate-200 px-4 py-3">
          <h2 className="text-sm font-semibold text-slate-600">Categories</h2>
        </div>
        {isLoading ? (
          <div className="px-4 py-6 text-sm text-slate-400">Loading…</div>
        ) : (data?.rows.length ?? 0) === 0 ? (
          <div className="px-4 py-8 text-center text-sm text-slate-400">
            No spend or budgets for {month}. Add a category below to start budgeting.
          </div>
        ) : (
          <div className="divide-y divide-slate-100">
            {data!.rows.map((r) => (
              <CategoryRow
                key={r.category}
                row={r}
                onSave={(limit) => setTarget.mutate({ category: r.category, monthly_limit: limit })}
                onRemove={() => removeTarget.mutate(r.category)}
              />
            ))}
          </div>
        )}

        <div className="flex flex-wrap items-end gap-2 border-t border-slate-200 px-4 py-3">
          <div className="flex-1 min-w-[10rem]">
            <label className="label">New category</label>
            <input className="input" placeholder="e.g. groceries" value={newCat} onChange={(e) => setNewCat(e.target.value)} />
          </div>
          <div className="w-32">
            <label className="label">Monthly limit €</label>
            <input className="input" type="number" min="0" step="0.01" value={newLimit} onChange={(e) => setNewLimit(e.target.value)} />
          </div>
          <button
            className="btn-primary"
            disabled={!newCat.trim() || newLimit === "" || setTarget.isPending}
            onClick={() => {
              setTarget.mutate(
                { category: newCat.trim(), monthly_limit: newLimit || "0" },
                { onSuccess: () => { setNewCat(""); setNewLimit(""); invalidate(); } },
              );
            }}
          >
            Add
          </button>
        </div>
      </div>
    </div>
  );
}

function CategoryRow({ row, onSave, onRemove }: { row: BudgetRow; onSave: (limit: string) => void; onRemove: () => void }) {
  const [limit, setLimit] = useState(row.budget);
  const [editing, setEditing] = useState(false);
  const pct = row.pct == null ? null : Math.min(row.pct, 100);
  const barColor = row.over ? "bg-rose-500" : (row.pct ?? 0) >= 80 ? "bg-amber-500" : "bg-brand-500";

  return (
    <div className="px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-slate-700">{row.category}</span>
          {row.untargeted && <span className="badge bg-slate-100 text-slate-500">no budget</span>}
          {row.over && <span className="badge bg-rose-100 text-rose-700">over</span>}
        </div>
        <div className="flex items-center gap-3 text-sm">
          <span className="text-slate-500">
            {money(row.actual)} <span className="text-slate-300">/</span>{" "}
            {editing ? (
              <input
                className="input inline-block w-24 py-1"
                type="number"
                min="0"
                step="0.01"
                autoFocus
                value={limit}
                onChange={(e) => setLimit(e.target.value)}
                onBlur={() => { setEditing(false); if (limit !== row.budget) onSave(limit || "0"); }}
                onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
              />
            ) : (
              <button className="font-medium text-slate-700 underline decoration-dotted" onClick={() => setEditing(true)}>
                {money(row.budget)}
              </button>
            )}
          </span>
          <span className={`w-20 text-right font-medium ${Number(row.remaining) < 0 ? "text-rose-600" : "text-emerald-600"}`}>
            {money(row.remaining)}
          </span>
          {!row.untargeted && (
            <button className="text-xs text-slate-400 hover:text-rose-600" onClick={onRemove} title="Remove budget">✕</button>
          )}
        </div>
      </div>
      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
        <div className={`h-full rounded-full ${barColor}`} style={{ width: `${pct ?? (Number(row.actual) > 0 ? 100 : 0)}%` }} />
      </div>
    </div>
  );
}
