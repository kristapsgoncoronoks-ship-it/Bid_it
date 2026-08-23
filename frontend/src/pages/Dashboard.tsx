import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { CategoryPie, SpendChart, VendorBar } from "../components/Charts";
import { EmptyState, QueryState, Skeleton } from "../components/ui";
import { api } from "../lib/api";
import { CHART_PALETTE as PALETTE, money } from "../lib/format";
import { DIMENSION_LABELS, type Dimensions } from "../lib/types";
import type {
  ByCategoryOut,
  DashboardData,
  DimensionBreakdown,
  SpendOverTimeOut,
  TopVendorsOut,
} from "../lib/types";

/**
 * The composed home dashboard (WO-16 / I1.1): "what needs me today". One
 * `GET /dashboard` projection — a section the server nulls (missing permission
 * or module off) renders NOTHING, so no role ever sees dead cards or 403s.
 * Every tile links to its filtered worklist; every figure is the canonical
 * service's own number (the SPA does no money math beyond formatting).
 */
export default function Dashboard() {
  const dash = useQuery<DashboardData>({
    queryKey: ["dashboard"],
    queryFn: async () => (await api.get("/dashboard")).data,
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Today</h1>
        <p className="text-sm text-slate-500">
          What needs you — approvals, captures to review, and money at risk.
        </p>
      </div>

      <NextActions />

      <QueryState query={dash} loading={<TilesSkeleton />} errorTitle="Couldn’t load your dashboard">
        {(d) => <DashboardBody d={d} />}
      </QueryState>
    </div>
  );
}

interface NextAction {
  kind: string;
  ref_id: string;
  title: string;
  detail: string;
  link: string;
  age_days: number | null;
  due_date: string | null;
  dismissible: boolean;
}

/** Next actions (WO-C): DERIVED work items — an offer to follow up, an
 * invoice to chase, uploads to confirm, a filing coming due. Every item
 * clears itself when the underlying work happens; Dismiss silences one item
 * permanently. Renders nothing for roles without planning rights (403) and
 * nothing when the list is empty — an empty card would be noise. */
function NextActions() {
  const [err, setErr] = useState<string | null>(null);
  const actions = useQuery<NextAction[] | null>({
    queryKey: ["next-actions"],
    queryFn: async () => {
      try {
        return (await api.get("/next-actions")).data;
      } catch {
        return null; // not a planner — the surface simply isn't theirs
      }
    },
  });
  const qc = useQueryClient();

  const dismiss = async (a: NextAction) => {
    try {
      await api.post("/next-actions/dismiss", { kind: a.kind, ref_id: a.ref_id });
      qc.invalidateQueries({ queryKey: ["next-actions"] });
    } catch {
      setErr("Couldn’t dismiss that item.");
    }
  };
  const complete = async (a: NextAction) => {
    try {
      await api.post(`/next-actions/deadlines/${a.ref_id}/complete`);
      qc.invalidateQueries({ queryKey: ["next-actions"] });
    } catch {
      setErr("Couldn’t mark that deadline done.");
    }
  };

  if (!Array.isArray(actions.data) || actions.data.length === 0) return null;
  return (
    <div className="card space-y-2 p-6">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
        Next actions
      </h2>
      {err && <div className="text-sm text-rose-600">{err}</div>}
      <ul className="divide-y divide-slate-100">
        {actions.data.map((a) => (
          <li key={`${a.kind}:${a.ref_id}`} className="flex items-start justify-between gap-3 py-2">
            <div className="min-w-0">
              <Link to={a.link} className="text-sm font-medium text-slate-700 hover:underline">
                {a.title}
              </Link>
              <div className="text-xs text-slate-500">{a.detail}</div>
            </div>
            <div className="flex shrink-0 gap-2">
              {a.kind === "deadline" && (
                <button className="btn-ghost text-xs" onClick={() => complete(a)}>
                  Done
                </button>
              )}
              {a.dismissible && (
                <button className="btn-ghost text-xs text-slate-400" onClick={() => dismiss(a)}>
                  Dismiss
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function TilesSkeleton() {
  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      {Array.from({ length: 4 }).map((_, i) => (
        <Skeleton key={i} className="h-28 w-full rounded-xl" />
      ))}
    </div>
  );
}

function DashboardBody({ d }: { d: DashboardData }) {
  const attention =
    (d.approvals?.total ?? 0) +
    (d.captures?.pending ?? 0) +
    (d.payables ? d.payables.overdue_count + d.payables.due_soon_count : 0) +
    (d.receivables && Number(d.receivables.overdue) > 0 ? 1 : 0);

  return (
    <div className="space-y-6">
      {/* Attention tiles — each links to its worklist. */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {d.approvals && (
          <Tile
            to="/invoices?workflow_state=in_approval"
            label="Waiting on you"
            value={String(d.approvals.total)}
            sub="approvals across the workspace"
            tone={d.approvals.total > 0 ? "rose" : "slate"}
          />
        )}
        {d.captures && (
          <Tile
            to="/captures"
            label="Captures to review"
            value={String(d.captures.pending)}
            sub={
              d.captures.low_confidence_fields > 0
                ? `${d.captures.low_confidence_fields} low-confidence fields`
                : "nothing flagged"
            }
            tone={d.captures.low_confidence_fields > 0 ? "amber" : "slate"}
          />
        )}
        {d.payables && (
          <Tile
            to="/cash-position"
            label="Overdue payables"
            value={money(d.payables.overdue_amount, d.payables.currency)}
            sub={
              `${d.payables.overdue_count} invoice${d.payables.overdue_count === 1 ? "" : "s"}` +
              (d.payables.other_currencies.length > 0
                ? ` · + ${d.payables.other_currencies.join(", ")}`
                : "")
            }
            tone={d.payables.overdue_count > 0 ? "rose" : "slate"}
          />
        )}
        {d.payables && (
          <Tile
            to="/cash-position"
            label="Due within 7 days"
            value={money(d.payables.due_soon_amount, d.payables.currency)}
            sub={`${d.payables.due_soon_count} invoice${d.payables.due_soon_count === 1 ? "" : "s"}`}
            tone={d.payables.due_soon_count > 0 ? "amber" : "slate"}
          />
        )}
        {d.receivables && (
          <Tile
            to="/issue/reports"
            label="Overdue receivables"
            value={money(d.receivables.overdue, d.receivables.currency)}
            sub={`${money(d.receivables.outstanding, d.receivables.currency)} outstanding`}
            tone={Number(d.receivables.overdue) > 0 ? "rose" : "slate"}
          />
        )}
        {d.cash && (
          <Tile
            to="/cash-position"
            label="Working-capital gap"
            value={money(d.cash.net_position, d.cash.currency)}
            sub="receivables − payables — not a bank balance"
            tone={Number(d.cash.net_position) >= 0 ? "emerald" : "amber"}
          />
        )}
      </div>

      {attention === 0 && (
        <div className="card p-0">
          <EmptyState
            title="Nothing needs you right now"
            description="No approvals waiting, no captures to review, nothing overdue. New work will appear here."
          />
        </div>
      )}

      {d.approvals && d.approvals.total > 0 && <ApprovalsCard a={d.approvals} />}

      {/* Spend snapshot — only for roles the server gave the reporting section. */}
      {d.cash && <SpendSnapshot />}
    </div>
  );
}

function Tile({
  to,
  label,
  value,
  sub,
  tone = "slate",
}: {
  to: string;
  label: string;
  value: string;
  sub?: string;
  tone?: "slate" | "rose" | "amber" | "emerald";
}) {
  const tones = {
    slate: "text-slate-800",
    rose: "text-rose-600",
    amber: "text-amber-600",
    emerald: "text-emerald-600",
  } as const;
  return (
    <Link
      to={to}
      className="group rounded-xl border border-slate-200 bg-white p-5 shadow-xs transition hover:border-brand-300 hover:shadow-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500"
    >
      <div className="flex items-center justify-between text-sm font-medium text-slate-500">
        {label}
        <span aria-hidden className="text-slate-300 transition group-hover:text-brand-500">
          →
        </span>
      </div>
      <div className={`mt-1 text-2xl font-semibold tracking-tight tabular-nums ${tones[tone]}`}>
        {value}
      </div>
      {sub && <div className="mt-1 truncate text-xs text-slate-400">{sub}</div>}
    </Link>
  );
}

function ApprovalsCard({ a }: { a: NonNullable<DashboardData["approvals"]> }) {
  const counters: { label: string; count: number | null; to: string }[] = [
    { label: "Expense reports", count: a.expense_reports, to: "/expenses" },
    { label: "Payment runs to check", count: a.payment_runs, to: "/payment-runs" },
    { label: "Supplier detail changes", count: a.vendor_changes, to: "/vendors" },
  ];
  const visible = counters.filter((c) => (c.count ?? 0) > 0);
  return (
    <div className="card p-0">
      <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
        <h2 className="text-sm font-semibold text-slate-600">Waiting on you</h2>
        {a.invoices !== null && (
          <Link
            to="/invoices?workflow_state=in_approval"
            className="text-sm text-brand-600 hover:underline"
          >
            All invoices awaiting approval →
          </Link>
        )}
      </div>
      {a.invoices && a.invoices.length > 0 && (
        <table className="w-full text-sm">
          <thead className="border-b border-slate-100 text-left text-xs uppercase tracking-wide text-slate-400">
            <tr>
              <th scope="col" className="px-4 py-2">Invoice</th>
              <th scope="col" className="px-4 py-2">Supplier</th>
              <th scope="col" className="px-4 py-2 text-right">Total</th>
              <th scope="col" className="px-4 py-2">
                <span className="sr-only">Action</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {a.invoices.map((inv) => (
              <tr
                key={inv.invoice_id}
                className="border-b border-slate-100 last:border-0 hover:bg-slate-50"
              >
                <td className="px-4 py-2 font-medium text-slate-700">{inv.invoice_number}</td>
                <td className="px-4 py-2 text-slate-500">{inv.vendor_name ?? "—"}</td>
                <td className="px-4 py-2 text-right tabular-nums">
                  {money(inv.total, inv.currency)}
                </td>
                <td className="px-4 py-2 text-right">
                  <Link
                    to={`/invoices/${inv.invoice_id}`}
                    className="font-medium text-brand-600 hover:underline"
                  >
                    Decide →
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {visible.length > 0 && (
        <div className="flex flex-wrap gap-x-6 gap-y-1 px-4 py-3 text-sm">
          {visible.map((c) => (
            <Link key={c.label} to={c.to} className="text-slate-600 hover:underline">
              <span className="font-semibold text-rose-600 tabular-nums">{c.count}</span> {c.label}{" "}
              →
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Spend snapshot — the pre-WO-16 analytics home, kept below the worklist for
// roles holding REPORT_READ (signalled by the server via a non-null `cash`
// section, so no query here can 403).
// ---------------------------------------------------------------------------

function useAnalytics<T>(path: string, key: string) {
  return useQuery<T>({
    queryKey: ["analytics", key],
    queryFn: async () => (await api.get(`/analytics/${path}`)).data,
  });
}

function SpendSnapshot() {
  const sot = useAnalytics<SpendOverTimeOut>("spend-over-time", "sot");
  const vendors = useAnalytics<TopVendorsOut>("top-vendors?limit=8", "vendors");
  const categories = useAnalytics<ByCategoryOut>("by-category", "categories");
  // C1.7/WO-24: each envelope names the single currency its `rows` are
  // scoped to (the AR-reports pattern) — pass it into money()/the charts
  // instead of relying on their EUR default, which used to silently mislabel
  // a non-EUR tenant's spend. Optional-chained throughout: defensive against
  // a still-loading/empty response, not just the happy path.
  const sotRows = sot.data?.rows ?? [];
  const sotCurrency = sot.data?.currency;
  const vendorRows = vendors.data?.rows ?? [];
  const vendorCurrency = vendors.data?.currency;
  const catRows = categories.data?.rows ?? [];
  const catCurrency = categories.data?.currency ?? "EUR";

  return (
    <div className="space-y-4">
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-semibold text-slate-600">Spend snapshot</h2>
        <Link to="/explore" className="text-sm text-brand-600 hover:underline">
          Explore →
        </Link>
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="card">
          <h3 className="mb-3 text-sm font-semibold text-slate-600">
            Spend over time {sotCurrency && <span className="text-slate-400">· {sotCurrency}</span>}
          </h3>
          {sotRows.length > 0 ? (
            <SpendChart data={sotRows} currency={sotCurrency} />
          ) : (
            <Empty />
          )}
        </div>
        <div className="card">
          <h3 className="mb-3 text-sm font-semibold text-slate-600">
            Top vendors{" "}
            {vendorCurrency && <span className="text-slate-400">· {vendorCurrency}</span>}
          </h3>
          {vendorRows.length > 0 ? (
            <VendorBar data={vendorRows} currency={vendorCurrency} />
          ) : (
            <Empty />
          )}
        </div>
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="card">
          <h3 className="mb-3 text-sm font-semibold text-slate-600">
            Spend by category{" "}
            {categories.data && <span className="text-slate-400">· {catCurrency}</span>}
          </h3>
          {catRows.length > 0 ? (
            <div className="flex items-center gap-4">
              <div className="flex-1">
                <CategoryPie data={catRows} currency={catCurrency} />
              </div>
              <ul className="w-40 space-y-1 text-sm">
                {catRows.slice(0, 8).map((c, i) => (
                  <li key={c.category} className="flex items-center gap-2">
                    <span
                      className="h-2.5 w-2.5 rounded-full"
                      style={{ background: PALETTE[i % PALETTE.length] }}
                    />
                    <span className="flex-1 truncate text-slate-600">{c.category}</span>
                    <span className="text-slate-400">{money(c.total, catCurrency)}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <Empty />
          )}
        </div>
        <SpendByDimension />
      </div>
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
  const currency = q.data?.currency ?? "EUR";
  const max = rows.reduce((m, r) => Math.max(m, Number(r.total)), 0) || 1;

  return (
    <div className="card">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-600">
          Spend by dimension {q.data && <span className="text-slate-400">· {currency}</span>}
        </h3>
        <select
          className="input w-40 py-1"
          value={dim}
          onChange={(e) => setDim(e.target.value as keyof Dimensions)}
        >
          {dims.map((dKey) => (
            <option key={dKey} value={dKey}>
              {DIMENSION_LABELS[dKey]}
            </option>
          ))}
        </select>
      </div>
      {rows.length > 0 ? (
        <ul className="space-y-1.5">
          {rows.map((r) => (
            <li key={r.value} className="flex items-center gap-3 text-sm">
              <span className="w-40 truncate text-slate-600" title={r.value}>
                {r.value}
              </span>
              <span className="relative h-5 flex-1 overflow-hidden rounded-sm bg-slate-100">
                <span
                  className="absolute inset-y-0 left-0 rounded-sm bg-brand-400/70"
                  style={{ width: `${(Number(r.total) / max) * 100}%` }}
                />
              </span>
              <span className="w-24 text-right tabular-nums text-slate-500">
                {money(r.total, currency)}
              </span>
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
