import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { EmptyState, PageHeader, QueryState, Skeleton } from "../components/ui";
import { api, downloadFile } from "../lib/api";
import { money, shortDate, STATUS_STYLES } from "../lib/format";
import { isAdminOrAbove } from "../lib/roles";
import type { InvoiceList, InvoiceStatus } from "../lib/types";

const STATUSES: (InvoiceStatus | "")[] = ["", "pending", "paid", "overdue", "draft"];
const PAGE_SIZE = 20;

// Human labels for the deep-linkable workflow-state filter (WO-16). The
// `in_approval` alias covers both live-chain states server-side.
const WORKFLOW_LABELS: Record<string, string> = {
  in_approval: "Awaiting approval",
  submitted: "Submitted",
  partially_approved: "Partially approved",
  approved: "Approved",
  scheduled_for_payment: "Scheduled for payment",
  partially_paid: "Partially paid",
  draft: "Draft",
  rejected: "Rejected",
  paid: "Paid",
};

const EXPORTS: { fmt: string; label: string }[] = [
  { fmt: "generic", label: "Accounting CSV" },
  { fmt: "xero", label: "Xero" },
  { fmt: "quickbooks", label: "QuickBooks" },
];

export default function Invoices() {
  const { user } = useAuth();
  // Filters initialise from the URL so dashboard tiles deep-link a FILTERED
  // worklist (WO-16); `workflow_state` lives in the URL only (chip to clear).
  const [searchParams, setSearchParams] = useSearchParams();
  const workflowState = searchParams.get("workflow_state") ?? "";
  const [status, setStatus] = useState<string>(searchParams.get("status") ?? "");
  const [q, setQ] = useState(searchParams.get("q") ?? "");
  const [page, setPage] = useState(1);

  const query = useQuery<InvoiceList>({
    queryKey: ["invoices", status, q, page, workflowState],
    queryFn: async () => {
      const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
      if (status) params.set("status", status);
      if (q) params.set("q", q);
      if (workflowState) params.set("workflow_state", workflowState);
      return (await api.get(`/invoices?${params.toString()}`)).data;
    },
  });
  const { data } = query;

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Invoices"
        actions={
          <>
            {isAdminOrAbove(user) && (
              <div className="flex items-center gap-1 text-sm">
                <span className="text-slate-400">Export:</span>
                {EXPORTS.map((e) => (
                  <button
                    key={e.fmt}
                    className="btn-ghost py-1"
                    title={`Export the invoice ledger for ${e.label}`}
                    onClick={() => downloadFile(`/export/accounting?fmt=${e.fmt}`, `invoices-${e.fmt}.csv`)}
                  >
                    {e.label}
                  </button>
                ))}
              </div>
            )}
            {/* Reachable from the list it came from rather than the global
                nav: somebody who has just deleted an invoice by mistake looks
                here, not in a sidebar. Deliberately NOT a nav item — a label
                containing "Invoices" also collides with the existing one. */}
            <Link to="/invoices/trash" className="btn-ghost">Deleted</Link>
            <Link to="/upload" className="btn-primary">Upload invoice</Link>
          </>
        }
      />

      <div className="flex flex-wrap items-end gap-3">
        <div>
          <label className="label">Search number</label>
          <input
            className="input w-56"
            placeholder="INV-2026-…"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setPage(1);
            }}
          />
        </div>
        <div>
          <label className="label">Status</label>
          <select
            className="input w-40"
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setPage(1);
            }}
          >
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s === "" ? "All" : s}
              </option>
            ))}
          </select>
        </div>
        {workflowState && (
          <span className="badge flex items-center gap-1.5 bg-brand-50 text-brand-700">
            {WORKFLOW_LABELS[workflowState] ?? workflowState}
            <button
              type="button"
              aria-label="Clear workflow filter"
              className="text-brand-500 hover:text-brand-700"
              onClick={() => {
                searchParams.delete("workflow_state");
                setSearchParams(searchParams, { replace: true });
                setPage(1);
              }}
            >
              ×
            </button>
          </span>
        )}
      </div>

      <div className="card overflow-x-auto p-0">
        <QueryState
          query={query}
          loading={<Skeleton className="m-4 h-40 w-[calc(100%-2rem)]" />}
          isEmpty={(d) => d.items.length === 0}
          empty={<EmptyState title="No invoices match these filters" />}
          errorTitle="Couldn’t load invoices"
        >
          {(d) => (
            <table className="w-full text-sm">
              <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-3">Number</th>
                  <th className="px-4 py-3">Issue date</th>
                  <th className="px-4 py-3">Status</th>
                  {workflowState && <th className="px-4 py-3">Workflow</th>}
                  <th className="px-4 py-3 text-right">Tax</th>
                  <th className="px-4 py-3 text-right">Total</th>
                </tr>
              </thead>
              <tbody>
                {d.items.map((inv) => (
                  <tr key={inv.id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50">
                    <td className="px-4 py-3">
                      <Link to={`/invoices/${inv.id}`} className="font-medium text-brand-600 hover:underline">
                        {inv.invoice_number}
                      </Link>
                    </td>
                    <td className="px-4 py-3 text-slate-500">{shortDate(inv.issue_date)}</td>
                    <td className="px-4 py-3">
                      <span className={`badge ${STATUS_STYLES[inv.status] ?? ""}`}>{inv.status}</span>
                    </td>
                    {workflowState && (
                      <td className="px-4 py-3">
                        <span className="badge bg-slate-100 text-slate-600">
                          {(inv.workflow_state && WORKFLOW_LABELS[inv.workflow_state]) ??
                            inv.workflow_state ??
                            "—"}
                        </span>
                      </td>
                    )}
                    <td className="px-4 py-3 text-right text-slate-500">{money(inv.tax_amount, inv.currency)}</td>
                    <td className="px-4 py-3 text-right font-medium">{money(inv.total, inv.currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </QueryState>
      </div>

      <div className="flex items-center justify-between text-sm text-slate-500">
        <span>{data ? `${data.total} invoices` : ""}</span>
        <div className="flex items-center gap-2">
          <button className="btn-ghost" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
            Prev
          </button>
          <span>
            Page {page} / {totalPages}
          </span>
          <button className="btn-ghost" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>
            Next
          </button>
        </div>
      </div>
    </div>
  );
}
