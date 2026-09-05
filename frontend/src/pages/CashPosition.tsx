import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { CashFlowChart } from "../components/Charts";
import { Badge, Card, PageHeader, QueryState, Skeleton, StatCard, type Tone } from "../components/ui";
import { api } from "../lib/api";
import { money, shortDate } from "../lib/format";
import type { ApAging, CashFlowPoint, CashPosition } from "../lib/types";

export default function CashPositionPage() {
  const cashPosition = useQuery<CashPosition>({
    queryKey: ["cash-position"],
    queryFn: async () => (await api.get("/analytics/cash-position")).data,
  });
  const apAging = useQuery<ApAging>({
    queryKey: ["ap-aging"],
    queryFn: async () => (await api.get("/analytics/ap-aging")).data,
  });
  const cashFlow = useQuery<CashFlowPoint[]>({
    queryKey: ["cash-flow"],
    queryFn: async () => (await api.get("/analytics/cash-flow?months=12")).data,
  });

  return (
    <div className="space-y-6">
      {/* WO-18 honesty copy — kept verbatim; the header is hoisted above the
          loading/error branch so the title (and this disclaimer) are stable
          while the figures load. */}
      <PageHeader
        title="Cash position"
        description="Receivables, payables, and bank reconciliation at a glance — a working-capital snapshot, not a bank balance. Net EUR."
      />

      <QueryState
        query={cashPosition}
        loading={<Skeleton className="h-40 w-full" />}
        errorTitle="Couldn’t load your cash position"
      >
        {(data) => {
          const cur = data.currency;
          const ar = data.receivables;
          const ap = data.payables;
          const rec = data.reconciliation;
          const net = Number(data.net_position);

          return (
            <div className="space-y-6">
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
                  label="Working-capital gap"
                  value={money(net, cur)}
                  sub={
                    (net >= 0 ? "receivables exceed payables" : "payables exceed receivables") +
                    " — not a bank balance"
                  }
                  accent={net >= 0 ? "brand" : "amber"}
                />
              </div>

              {/* Cash-flow trend */}
              {cashFlow.data && cashFlow.data.length > 0 && (
                <Card title="Cash flow, last 12 months (historical)">
                  <p className="mb-3 text-xs text-slate-400">
                    Settled inflow vs. outflow by month — a look back, not a forecast.
                  </p>
                  <CashFlowChart data={cashFlow.data} />
                </Card>
              )}

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
                {ap.other_currencies?.length ? (
                  // PERF-002 (2026-09-05): the amounts above are {cur} only. Payables in
                  // other currencies used to be summed into them; now they are named.
                  <p className="mt-2 text-xs text-slate-500">
                    Also open in {ap.other_currencies.join(", ")} — not included in the {cur}{" "}
                    figures (no conversion is recorded for them).
                  </p>
                ) : null}
                {apAging.data && <ApWorklist aging={apAging.data} />}
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
        }}
      </QueryState>
    </div>
  );
}

function ApWorklist({ aging }: { aging: ApAging }) {
  // Show only what needs attention: overdue first, then due-soon.
  const rows = aging.items
    .filter((i) => i.status === "overdue" || i.bucket === "due_soon")
    .sort((a, b) => (a.status === "overdue" ? 0 : 1) - (b.status === "overdue" ? 0 : 1));
  if (rows.length === 0)
    return (
      <p className="mt-4 text-xs text-slate-400">Nothing due soon or overdue. 🎉</p>
    );

  // The summary amounts are single-currency (WO-8): labelled with the server's
  // currency; anything it could not fold in is called out, never summed.
  return (
    <div className="mt-4">
      <div className="mb-2 flex items-center gap-3 text-xs font-semibold uppercase tracking-wide text-slate-400">
        <span>Needs attention</span>
        {aging.overdue_count > 0 && (
          <span className="text-rose-600">
            {aging.overdue_count} overdue · {money(aging.overdue_amount, aging.currency)}
          </span>
        )}
        {aging.due_soon_count > 0 && (
          <span className="text-amber-600">
            {aging.due_soon_count} due soon · {money(aging.due_soon_amount, aging.currency)}
          </span>
        )}
        {aging.other_currencies.length > 0 && (
          <span>+ amounts in {aging.other_currencies.join(", ")} (listed below)</span>
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
