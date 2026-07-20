import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { CategoryPie, SpendChart, VendorBar } from "../components/Charts";
import { KpiCard } from "../components/KpiCard";
import { api } from "../lib/api";
import { CHART_PALETTE as PALETTE, money, STATUS_STYLES } from "../lib/format";
import { DIMENSION_LABELS, type Dimensions } from "../lib/types";
import type {
  CategorySpend,
  DimensionBreakdown,
  StatusBucket,
  Summary,
  TimeBucket,
  VendorSpend,
} from "../lib/types";

function useAnalytics<T>(path: string, key: string) {
  return useQuery<T>({
    queryKey: ["analytics", key],
    queryFn: async () => (await api.get(`/analytics/${path}`)).data,
  });
}


export default function Dashboard() {
  const summary = useAnalytics<Summary>("summary", "summary");
  const sot = useAnalytics<TimeBucket[]>("spend-over-time", "sot");
  const vendors = useAnalytics<VendorSpend[]>("top-vendors?limit=8", "vendors");
  const categories = useAnalytics<CategorySpend[]>("by-category", "categories");
  const statuses = useAnalytics<StatusBucket[]>("by-status", "statuses");

  const s = summary.data;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Spend overview</h1>
        <p className="text-sm text-slate-500">Totals include tax. Amounts in EUR.</p>
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <KpiCard label="Total spend" value={s ? money(s.total_spend) : "—"} sub={s ? `${s.total_invoices} invoices` : ""} />
        <KpiCard label="Unpaid" value={s ? money(s.unpaid_amount) : "—"} accent="rose" sub="pending + overdue" />
        <KpiCard label="Tax booked" value={s ? money(s.total_tax) : "—"} accent="amber" />
        <KpiCard label="Avg invoice" value={s ? money(s.avg_invoice) : "—"} accent="emerald" sub={s ? `${s.vendor_count} vendors` : ""} />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="card">
          <h2 className="mb-3 text-sm font-semibold text-slate-600">Spend over time</h2>
          {sot.data && sot.data.length > 0 ? (
            <SpendChart data={sot.data} />
          ) : (
            <Empty />
          )}
        </div>
        <div className="card">
          <h2 className="mb-3 text-sm font-semibold text-slate-600">Top vendors</h2>
          {vendors.data && vendors.data.length > 0 ? <VendorBar data={vendors.data} /> : <Empty />}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="card">
          <h2 className="mb-3 text-sm font-semibold text-slate-600">Spend by category</h2>
          {categories.data && categories.data.length > 0 ? (
            <div className="flex items-center gap-4">
              <div className="flex-1">
                <CategoryPie data={categories.data} />
              </div>
              <ul className="w-40 space-y-1 text-sm">
                {categories.data.slice(0, 8).map((c, i) => (
                  <li key={c.category} className="flex items-center gap-2">
                    <span className="h-2.5 w-2.5 rounded-full" style={{ background: PALETTE[i % PALETTE.length] }} />
                    <span className="flex-1 truncate text-slate-600">{c.category}</span>
                    <span className="text-slate-400">{money(c.total)}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <Empty />
          )}
        </div>
        <div className="card">
          <h2 className="mb-3 text-sm font-semibold text-slate-600">By status</h2>
          <div className="space-y-2">
            {statuses.data && statuses.data.length > 0 ? (
              statuses.data.map((b) => (
                <div key={b.status} className="flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2">
                  <span className={`badge ${STATUS_STYLES[b.status] ?? "bg-slate-100 text-slate-600"}`}>
                    {b.status}
                  </span>
                  <span className="text-sm text-slate-500">{b.count} invoices</span>
                  <span className="text-sm font-medium text-slate-700">{money(b.total)}</span>
                </div>
              ))
            ) : (
              <Empty />
            )}
          </div>
        </div>
      </div>

      <SpendByDimension />
    </div>
  );
}

// Spend grouped by a cost-allocation dimension (cost center, vehicle, …). A
// simple selector switches dimension; bars are proportional to the top value.
function SpendByDimension() {
  const dims = Object.keys(DIMENSION_LABELS) as (keyof Dimensions)[];
  const [dim, setDim] = useState<keyof Dimensions>("cost_center");
  const q = useQuery<DimensionBreakdown>({
    queryKey: ["analytics", "by-dimension", dim],
    queryFn: async () => (await api.get(`/analytics/by-dimension?dimension=${dim}`)).data,
  });
  const rows = q.data?.rows ?? [];
  const max = rows.reduce((m, r) => Math.max(m, Number(r.total)), 0) || 1;

  return (
    <div className="card">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-600">Spend by dimension</h2>
        <select className="input w-40 py-1" value={dim} onChange={(e) => setDim(e.target.value as keyof Dimensions)}>
          {dims.map((d) => <option key={d} value={d}>{DIMENSION_LABELS[d]}</option>)}
        </select>
      </div>
      {rows.length > 0 ? (
        <ul className="space-y-1.5">
          {rows.map((r) => (
            <li key={r.value} className="flex items-center gap-3 text-sm">
              <span className="w-40 truncate text-slate-600" title={r.value}>{r.value}</span>
              <span className="relative h-5 flex-1 overflow-hidden rounded bg-slate-100">
                <span className="absolute inset-y-0 left-0 rounded bg-brand-400/70" style={{ width: `${(Number(r.total) / max) * 100}%` }} />
              </span>
              <span className="w-24 text-right tabular-nums text-slate-500">{money(r.total)}</span>
              <span className="w-16 text-right text-xs text-slate-400">{r.invoice_count} inv</span>
            </li>
          ))}
        </ul>
      ) : (
        <Empty />
      )}
    </div>
  );
}

function Empty() {
  return (
    <div className="grid h-[220px] place-items-center text-sm text-slate-400">
      No data yet — upload or add an invoice.
    </div>
  );
}
