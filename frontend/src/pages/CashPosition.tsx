import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Badge, Card, StatCard, type Tone } from "../components/ui";
import { api } from "../lib/api";
import { money, shortDate } from "../lib/format";
import type { ApAging, CashPosition } from "../lib/types";

export default function CashPositionPage() {
  const { data, isLoading } = useQuery<CashPosition>({
    queryKey: ["cash-position"],
    queryFn: async () => (await api.get("/analytics/cash-position")).data,
  });
  const apAging = useQuery<ApAging>({
    queryKey: ["ap-aging"],
    queryFn: async () => (await api.get("/analytics/ap-aging")).data,
  });

  if (isLoading || !data) return <div className="text-slate-400">Loading…</div>;

  const cur = data.currency;
  const ar = data.receivables;
  const ap = data.payables;
  const rec = data.reconciliation;
  const net = Number(data.net_position);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Cash position</h1>
        <p className="text-slate-500">
          Receivables, payables, and bank reconciliation at a glance. Net EUR.
        </p>
      </div>

      {/* Headline */}
      <div className="grid gap-4 sm:grid-cols-3">
        <StatCard
          label="Owed to you (receivable)"
          value={money(ar.outstanding, cur)}
          sub={`${money(ar.overdue, cur)} overdue`}
          accent="emerald"
        />
        <StatCard
          label="You owe (payable)"
          value={money(ap.outstanding, cur)}
          sub={`${money(ap.overdue, cur)} overdue`}
          accent="rose"
        />
        <StatCard
          label="Net position"
          value={money(net, cur)}
          sub={net >= 0 ? "receivables exceed payables" : "payables exceed receivables"}
          accent={net >= 0 ? "brand" : "amber"}
        />
      </div>

      {/* Receivables */}
      <Card title="Receivables">
        <div className="grid gap-4 sm:grid-cols-4">
          <StatCard label="Outstanding" value={money(ar.outstanding, cur)} />
          <StatCard label="Overdue" value={money(ar.overdue, cur)} accent="rose" />
          <StatCard
            label="Avg days to pay"
            value={ar.avg_days_to_pay != null ? Math.round(ar.avg_days_to_pay) : "—"}
          />
        </div>
        <div className="mt-4">
          <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
            Aging of outstanding
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-slate-400">
                <th className="py-1">Bucket</th>
                <th className="py-1 text-right">Invoices</th>
                <th className="py-1 text-right">Outstanding</th>
              </tr>
            </thead>
            <tbody>
              {ar.aging.map((b) => (
                <tr key={b.label} className="border-t border-slate-100">
                  <td className="py-1">{b.label}</td>
                  <td className="py-1 text-right tabular-nums">{b.count}</td>
                  <td className="py-1 text-right tabular-nums">{money(b.outstanding, cur)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {/* Payables */}
      <Card
        title="Payables"
        actions={
          <Link to="/payment-runs" className="text-sm text-brand-600 hover:underline">
            Payment runs →
          </Link>
        }
      >
        <div className="grid gap-4 sm:grid-cols-4">
          <StatCard label="Outstanding" value={money(ap.outstanding, cur)} />
          <StatCard label="Overdue" value={money(ap.overdue, cur)} accent="rose" />
          <StatCard label="Scheduled" value={ap.scheduled} sub="ready to pay" />
          <StatCard label="Open invoices" value={ap.count} sub={`${ap.in_run} in a run`} />
        </div>
        {apAging.data && <ApWorklist aging={apAging.data} currency={cur} />}
      </Card>

      {/* Reconciliation */}
      <Card
        title="Bank reconciliation"
        actions={
          <Link to="/reconciliation" className="text-sm text-brand-600 hover:underline">
            Reconcile →
          </Link>
        }
      >
        <div className="grid gap-4 sm:grid-cols-4">
          <StatCard
            label="Unmatched lines"
            value={rec.unmatched}
            sub={money(rec.unmatched_amount, cur)}
            accent={rec.unmatched > 0 ? "amber" : "slate"}
          />
          <StatCard label="Matched" value={rec.matched} accent="emerald" />
          <StatCard label="Ignored" value={rec.ignored} />
        </div>
      </Card>
    </div>
  );
}

function ApWorklist({ aging, currency }: { aging: ApAging; currency: string }) {
  // Show only what needs attention: overdue first, then due-soon.
  const rows = aging.items
    .filter((i) => i.status === "overdue" || i.bucket === "due_soon")
    .sort((a, b) => (a.status === "overdue" ? 0 : 1) - (b.status === "overdue" ? 0 : 1));
  if (rows.length === 0)
    return (
      <p className="mt-4 text-xs text-slate-400">Nothing due soon or overdue. 🎉</p>
    );

  return (
    <div className="mt-4">
      <div className="mb-2 flex items-center gap-3 text-xs font-semibold uppercase tracking-wide text-slate-400">
        <span>Needs attention</span>
        {aging.overdue_count > 0 && (
          <span className="text-rose-600">
            {aging.overdue_count} overdue · {money(aging.overdue_amount, currency)}
          </span>
        )}
        {aging.due_soon_count > 0 && (
          <span className="text-amber-600">
            {aging.due_soon_count} due soon · {money(aging.due_soon_amount, currency)}
          </span>
        )}
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-slate-400">
            <th className="py-1">Invoice</th>
            <th className="py-1">Supplier</th>
            <th className="py-1">Due</th>
            <th className="py-1 text-right">Outstanding</th>
            <th className="py-1">Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((i) => {
            const tone: Tone = i.status === "overdue" ? "danger" : "warning";
            const label =
              i.status === "overdue" ? `${i.days_overdue}d overdue` : "due soon";
            return (
              <tr key={i.id} className="border-t border-slate-100">
                <td className="py-1">{i.invoice_number}</td>
                <td className="py-1 text-slate-500">{i.vendor_name ?? "—"}</td>
                <td className="py-1 text-slate-500">{shortDate(i.due_date)}</td>
                <td className="py-1 text-right tabular-nums">
                  {money(i.outstanding, i.currency)}
                </td>
                <td className="py-1">
                  <Badge tone={tone}>{label}</Badge>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
