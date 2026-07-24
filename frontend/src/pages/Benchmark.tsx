import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { KpiCard } from "../components/KpiCard";
import { api } from "../lib/api";
import { money } from "../lib/format";
import type { CombinedBenchmark, SupplierBenchmark } from "../lib/types";

type Tab = "independent" | "combined";

export default function Benchmark() {
  const [tab, setTab] = useState<Tab>("combined");

  const suppliers = useQuery<SupplierBenchmark[]>({
    queryKey: ["benchmark", "suppliers"],
    queryFn: async () => (await api.get("/analytics/supplier-benchmark")).data,
  });
  const combined = useQuery<CombinedBenchmark>({
    queryKey: ["benchmark", "combined"],
    queryFn: async () => (await api.get("/analytics/combined-benchmark")).data,
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Supplier benchmark</h1>
        <p className="text-sm text-slate-500">
          Each supplier on its own, and compared head-to-head on price. Basis: effective unit
          price = spend ÷ quantity within a category. Indicative — EUR.
        </p>
      </div>

      <div className="flex gap-1 rounded-lg bg-slate-100 p-1 text-sm w-fit">
        {(["combined", "independent"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`rounded-md px-4 py-1.5 font-medium capitalize ${
              tab === t ? "bg-white text-brand-700 shadow-xs" : "text-slate-500 hover:text-slate-700"
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === "combined" ? (
        <CombinedView data={combined.data} loading={combined.isLoading} />
      ) : (
        <IndependentView rows={suppliers.data} loading={suppliers.isLoading} />
      )}
    </div>
  );
}

function CombinedView({ data, loading }: { data?: CombinedBenchmark; loading: boolean }) {
  if (loading) return <Loading />;
  if (!data || data.categories.length === 0) return <Empty />;
  const s = data.summary;

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <KpiCard
          label="Savings opportunity"
          value={money(s.total_savings_opportunity)}
          accent="emerald"
          sub="if each category bought at its cheapest rate"
        />
        <KpiCard label="Suppliers" value={String(s.supplier_count)} />
        <KpiCard label="Categories" value={String(s.categories_analyzed)} />
        <KpiCard
          label="Contested categories"
          value={String(s.multi_supplier_categories)}
          accent="amber"
          sub="more than one supplier"
        />
      </div>

      <div className="space-y-4">
        {data.categories.map((c) => (
          <div key={c.category} className="card">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <div>
                <h3 className="text-base font-semibold capitalize">{c.category}</h3>
                <p className="text-xs text-slate-500">
                  {c.supplier_count} supplier{c.supplier_count !== 1 ? "s" : ""} · combined avg{" "}
                  {money(c.combined_avg_unit)}/unit · cheapest{" "}
                  <span className="font-medium text-emerald-600">{c.cheapest_vendor_name}</span> @{" "}
                  {money(c.cheapest_unit)}
                </p>
              </div>
              {Number(c.savings_opportunity) > 0 && (
                <span className="badge bg-emerald-100 text-emerald-700">
                  save {money(c.savings_opportunity)}
                </span>
              )}
            </div>

            <table className="mt-3 w-full text-sm">
              <thead className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="py-2">Supplier</th>
                  <th className="py-2 text-right">Unit price</th>
                  <th className="py-2 text-right">vs avg</th>
                  <th className="py-2 text-right">Spend</th>
                  <th className="py-2 text-right">Overpay vs cheapest</th>
                </tr>
              </thead>
              <tbody>
                {c.suppliers.map((sup) => {
                  const dev = Number(sup.deviation_pct);
                  return (
                    <tr key={sup.vendor_id} className="border-b border-slate-100 last:border-0">
                      <td className="py-2">
                        {sup.vendor_name}
                        {sup.is_cheapest && (
                          <span className="badge ml-2 bg-emerald-100 text-emerald-700">cheapest</span>
                        )}
                      </td>
                      <td className="py-2 text-right font-medium">{money(sup.unit_price)}</td>
                      <td
                        className={`py-2 text-right ${
                          dev > 0 ? "text-rose-600" : dev < 0 ? "text-emerald-600" : "text-slate-400"
                        }`}
                      >
                        {dev > 0 ? "+" : ""}
                        {sup.deviation_pct}%
                      </td>
                      <td className="py-2 text-right text-slate-500">{money(sup.spend)}</td>
                      <td className="py-2 text-right">
                        {Number(sup.overspend_vs_cheapest) > 0 ? (
                          <span className="text-rose-600">{money(sup.overspend_vs_cheapest)}</span>
                        ) : (
                          <span className="text-slate-300">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ))}
      </div>
    </div>
  );
}

function IndependentView({ rows, loading }: { rows?: SupplierBenchmark[]; loading: boolean }) {
  if (loading) return <Loading />;
  if (!rows || rows.length === 0) return <Empty />;

  return (
    <div className="card overflow-x-auto p-0">
      <table className="w-full text-sm">
        <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
          <tr>
            <th className="px-4 py-3">Supplier</th>
            <th className="px-4 py-3 text-right">Spend</th>
            <th className="px-4 py-3 text-right">Share</th>
            <th className="px-4 py-3 text-right">Invoices</th>
            <th className="px-4 py-3 text-right">Avg invoice</th>
            <th className="px-4 py-3 text-right">Categories</th>
            <th className="px-4 py-3 text-right">Eff. tax</th>
            <th className="px-4 py-3 text-right">Paid</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.vendor_id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50">
              <td className="px-4 py-3 font-medium">
                {r.vendor_name}
                {r.country && <span className="ml-2 text-xs text-slate-400">{r.country}</span>}
              </td>
              <td className="px-4 py-3 text-right font-medium">{money(r.total_spend)}</td>
              <td className="px-4 py-3 text-right text-slate-500">{r.spend_share}%</td>
              <td className="px-4 py-3 text-right text-slate-500">{r.invoice_count}</td>
              <td className="px-4 py-3 text-right text-slate-500">{money(r.avg_invoice)}</td>
              <td className="px-4 py-3 text-right text-slate-500">{r.category_count}</td>
              <td className="px-4 py-3 text-right text-slate-500">{r.effective_tax_rate}%</td>
              <td className="px-4 py-3 text-right">
                <PaidBar ratio={Number(r.paid_ratio)} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PaidBar({ ratio }: { ratio: number }) {
  const pct = Math.round(ratio * 100);
  return (
    <div className="inline-flex items-center gap-2">
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-slate-200">
        <div className="h-full rounded-full bg-emerald-500" style={{ width: `${pct}%` }} />
      </div>
      <span className="w-9 text-xs text-slate-500">{pct}%</span>
    </div>
  );
}

function Loading() {
  return <div className="card text-sm text-slate-400">Loading benchmark…</div>;
}
function Empty() {
  return (
    <div className="card grid h-40 place-items-center text-sm text-slate-400">
      Not enough data yet — add invoices from a few suppliers.
    </div>
  );
}
