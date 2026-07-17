import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../lib/api";
import { money, shortDate, STATUS_STYLES } from "../lib/format";
import type { InvoiceDetail, InvoiceStatus } from "../lib/types";

const STATUSES: InvoiceStatus[] = ["draft", "pending", "paid", "overdue"];

export default function InvoiceDetailPage() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();

  const { data: inv, isLoading } = useQuery<InvoiceDetail>({
    queryKey: ["invoice", id],
    queryFn: async () => (await api.get(`/invoices/${id}`)).data,
    enabled: !!id,
  });

  const setStatus = useMutation({
    mutationFn: async (status: InvoiceStatus) =>
      (await api.patch(`/invoices/${id}`, { status })).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["invoice", id] });
      qc.invalidateQueries({ queryKey: ["invoices"] });
      qc.invalidateQueries({ queryKey: ["analytics"] });
    },
  });

  if (isLoading || !inv) return <div className="text-slate-400">Loading…</div>;

  return (
    <div className="space-y-6">
      <Link to="/invoices" className="text-sm text-brand-600 hover:underline">
        ← Back to invoices
      </Link>

      <div className="card">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold">{inv.invoice_number}</h1>
            <p className="text-slate-500">{inv.vendor_name}</p>
          </div>
          <div className="text-right">
            <div className="text-2xl font-semibold">{money(inv.total, inv.currency)}</div>
            <span className={`badge mt-1 ${STATUS_STYLES[inv.status] ?? ""}`}>{inv.status}</span>
          </div>
        </div>

        <dl className="mt-5 grid grid-cols-2 gap-4 text-sm md:grid-cols-4">
          <Field label="Issue date" value={shortDate(inv.issue_date)} />
          <Field label="Due date" value={shortDate(inv.due_date)} />
          <Field label="Subtotal" value={money(inv.subtotal, inv.currency)} />
          <Field label="Tax" value={money(inv.tax_amount, inv.currency)} />
        </dl>

        <div className="mt-5">
          <label className="label">Change status</label>
          <div className="flex gap-2">
            {STATUSES.map((s) => (
              <button
                key={s}
                disabled={setStatus.isPending || s === inv.status}
                onClick={() => setStatus.mutate(s)}
                className={`btn ${
                  s === inv.status ? "bg-brand-500 text-white" : "border border-slate-300 bg-white hover:bg-slate-50"
                }`}
              >
                {s}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="card overflow-hidden p-0">
        <table className="w-full text-sm">
          <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-3">Description</th>
              <th className="px-4 py-3">Category</th>
              <th className="px-4 py-3 text-right">Qty</th>
              <th className="px-4 py-3 text-right">Unit</th>
              <th className="px-4 py-3 text-right">Tax %</th>
              <th className="px-4 py-3 text-right">Amount</th>
            </tr>
          </thead>
          <tbody>
            {inv.line_items.map((li) => (
              <tr key={li.id} className="border-b border-slate-100 last:border-0">
                <td className="px-4 py-3">{li.description}</td>
                <td className="px-4 py-3">
                  <span className="badge bg-slate-100 text-slate-600">{li.category}</span>
                </td>
                <td className="px-4 py-3 text-right text-slate-500">{Number(li.quantity)}</td>
                <td className="px-4 py-3 text-right text-slate-500">{money(li.unit_price, inv.currency)}</td>
                <td className="px-4 py-3 text-right text-slate-500">{Number(li.tax_rate)}%</td>
                <td className="px-4 py-3 text-right font-medium">{money(li.amount, inv.currency)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {inv.notes && <div className="card text-sm text-slate-600">{inv.notes}</div>}
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-slate-400">{label}</dt>
      <dd className="mt-0.5 font-medium text-slate-700">{value}</dd>
    </div>
  );
}
