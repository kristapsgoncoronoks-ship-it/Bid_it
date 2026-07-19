import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { api, apiError, downloadFile } from "../lib/api";
import { EXPENSE_STATUS_STYLES, money, shortDate } from "../lib/format";
import type { ExpenseReportDetail } from "../lib/types";

export default function ExpenseDetail() {
  const { id } = useParams<{ id: string }>();
  const { user } = useAuth();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const fileRef = useRef<HTMLInputElement>(null);
  const uploadItemId = useRef<string | null>(null);

  const { data: r, isLoading } = useQuery<ExpenseReportDetail>({
    queryKey: ["expense", id],
    queryFn: async () => (await api.get(`/expenses/${id}`)).data,
    enabled: !!id,
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["expense", id] });
    qc.invalidateQueries({ queryKey: ["expenses"] });
  };
  const act = useMutation({
    mutationFn: async (p: { path: string; body?: unknown }) => (await api.post(`/expenses/${id}/${p.path}`, p.body ?? {})).data,
    onSuccess: invalidate,
    onError: (e) => alert(apiError(e)),
  });
  const del = useMutation({
    mutationFn: async () => api.delete(`/expenses/${id}`),
    onSuccess: () => { invalidate(); navigate("/expenses"); },
  });
  const uploadReceipt = useMutation({
    mutationFn: async (v: { itemId: string; file: File }) => {
      const form = new FormData();
      form.append("file", v.file);
      return (await api.post(`/expenses/${id}/items/${v.itemId}/receipt`, form)).data;
    },
    onSuccess: invalidate,
    onError: (e) => alert(apiError(e)),
  });

  if (isLoading || !r) return <div className="text-slate-400">Loading…</div>;

  const isManager = user?.role === "owner";
  const isOwnerOfReport = r.employee_id === user?.id;
  const canSubmit = isOwnerOfReport && r.status === "draft";
  const canDecide = isManager && (r.status === "submitted" || r.status === "approved");

  return (
    <div className="space-y-6">
      <Link to="/expenses" className="text-sm text-brand-600 hover:underline">← Back to expenses</Link>

      <div className="card">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold">{r.title}</h1>
            <p className="text-sm text-slate-500">{r.employee_name}{r.submitted_at && ` · submitted ${shortDate(r.submitted_at)}`}</p>
          </div>
          <div className="text-right">
            <div className="text-2xl font-semibold">{money(r.total, r.currency)}</div>
            <span className={`badge mt-1 ${EXPENSE_STATUS_STYLES[r.status] ?? ""}`}>{r.status}</span>
          </div>
        </div>
        <dl className="mt-4 grid grid-cols-2 gap-4 text-sm md:grid-cols-4">
          <Field label="Reclaimable VAT" value={money(r.vat_total, r.currency)} />
          {r.total_eur && r.currency !== "EUR" && <Field label="Total (EUR, ECB)" value={money(r.total_eur)} />}
          {r.decided_by && <Field label={r.status} value={`${r.decided_by}`} />}
        </dl>

        <div className="mt-4 flex flex-wrap gap-2">
          {canSubmit && <button className="btn-primary" onClick={() => act.mutate({ path: "submit" })}>Submit for approval</button>}
          {canSubmit && <button className="btn-ghost text-rose-600" onClick={() => del.mutate()}>Delete draft</button>}
          {isManager && r.status === "submitted" && (
            <>
              <button className="btn bg-emerald-600 text-white hover:bg-emerald-700" onClick={() => act.mutate({ path: "decision", body: { action: "approve" } })}>Approve</button>
              <button className="btn border border-rose-300 bg-white text-rose-600 hover:bg-rose-50" onClick={() => act.mutate({ path: "decision", body: { action: "reject" } })}>Reject</button>
            </>
          )}
          {isManager && r.status === "approved" && (
            <button className="btn bg-emerald-600 text-white hover:bg-emerald-700" onClick={() => act.mutate({ path: "decision", body: { action: "reimburse" } })}>Mark reimbursed</button>
          )}
          <button className="btn-ghost" onClick={() => downloadFile(`/expenses/${id}/pdf`, `expense-${r.title}.pdf`)}>Download PDF</button>
          {!canDecide && !canSubmit && r.status !== "draft" && <span className="self-center text-xs text-slate-400">No actions available.</span>}
        </div>
      </div>

      <input
        ref={fileRef}
        type="file"
        accept=".pdf,.png,.jpg,.jpeg"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f && uploadItemId.current) uploadReceipt.mutate({ itemId: uploadItemId.current, file: f });
          e.target.value = "";
        }}
      />

      <div className="card overflow-x-auto p-0">
        <table className="w-full text-sm">
          <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-3">Date</th>
              <th className="px-4 py-3">Category</th>
              <th className="px-4 py-3">Description</th>
              <th className="px-4 py-3">Merchant</th>
              <th className="px-4 py-3 text-right">VAT</th>
              <th className="px-4 py-3 text-right">Amount</th>
              <th className="px-4 py-3">Receipt</th>
            </tr>
          </thead>
          <tbody>
            {r.items.map((it) => (
              <tr key={it.id} className="border-b border-slate-100 last:border-0">
                <td className="px-4 py-3 text-slate-500">{shortDate(it.spend_date)}</td>
                <td className="px-4 py-3"><span className="badge bg-slate-100 text-slate-600">{it.category}</span></td>
                <td className="px-4 py-3">{it.description}</td>
                <td className="px-4 py-3 text-slate-500">{it.merchant || "—"}</td>
                <td className="px-4 py-3 text-right text-slate-500">{money(it.vat_amount, r.currency)}</td>
                <td className="px-4 py-3 text-right font-medium">{money(it.amount, r.currency)}</td>
                <td className="px-4 py-3">
                  {it.has_receipt ? (
                    <button className="text-brand-600 hover:underline" onClick={() => downloadFile(`/expenses/${id}/items/${it.id}/receipt`, `receipt-${it.id}`)}>view</button>
                  ) : canSubmit ? (
                    <button className="text-slate-500 hover:underline" onClick={() => { uploadItemId.current = it.id; fileRef.current?.click(); }}>attach</button>
                  ) : (
                    <span className="text-slate-300">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
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
