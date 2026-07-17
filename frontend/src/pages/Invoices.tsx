import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { money, shortDate, STATUS_STYLES } from "../lib/format";
import type { InvoiceList, InvoiceStatus } from "../lib/types";

const STATUSES: (InvoiceStatus | "")[] = ["", "pending", "paid", "overdue", "draft"];
const PAGE_SIZE = 20;

export default function Invoices() {
  const [status, setStatus] = useState<string>("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);

  const { data, isLoading } = useQuery<InvoiceList>({
    queryKey: ["invoices", status, q, page],
    queryFn: async () => {
      const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
      if (status) params.set("status", status);
      if (q) params.set("q", q);
      return (await api.get(`/invoices?${params.toString()}`)).data;
    },
  });

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Invoices</h1>
        <Link to="/upload" className="btn-primary">Upload invoice</Link>
      </div>

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
      </div>

      <div className="card overflow-hidden p-0">
        <table className="w-full text-sm">
          <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-3">Number</th>
              <th className="px-4 py-3">Issue date</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3 text-right">Tax</th>
              <th className="px-4 py-3 text-right">Total</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr>
                <td colSpan={5} className="px-4 py-10 text-center text-slate-400">Loading…</td>
              </tr>
            )}
            {data?.items.map((inv) => (
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
                <td className="px-4 py-3 text-right text-slate-500">{money(inv.tax_amount, inv.currency)}</td>
                <td className="px-4 py-3 text-right font-medium">{money(inv.total, inv.currency)}</td>
              </tr>
            ))}
            {data && data.items.length === 0 && !isLoading && (
              <tr>
                <td colSpan={5} className="px-4 py-10 text-center text-slate-400">No invoices found.</td>
              </tr>
            )}
          </tbody>
        </table>
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
