import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Field } from "../components/Field";
import { useToast } from "../components/Toast";
import { useAuth } from "../auth/AuthContext";
import { api, apiError, downloadFile } from "../lib/api";
import { EXPENSE_STATUS_STYLES, money, shortDate } from "../lib/format";
import type { ExpenseComment, ExpenseReportDetail, ExpenseTransaction } from "../lib/types";

export default function ExpenseDetail() {
  const { id } = useParams<{ id: string }>();
  const { user } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
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
    onError: (e) => toast.error(apiError(e)),
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
    onError: (e) => toast.error(apiError(e)),
  });
  const patchItem = useMutation({
    mutationFn: async (v: { itemId: string; comment: string }) =>
      (await api.patch(`/expenses/${id}/items/${v.itemId}`, { comment: v.comment })).data,
    onSuccess: invalidate,
    onError: (e) => toast.error(apiError(e)),
  });
  const match = useMutation({
    mutationFn: async (v: { itemId: string; transactionId: string }) =>
      (await api.post(`/expenses/${id}/items/${v.itemId}/match`, { transaction_id: v.transactionId })).data,
    onSuccess: invalidate,
    onError: (e) => toast.error(apiError(e)),
  });
  const unmatch = useMutation({
    mutationFn: async (itemId: string) => (await api.delete(`/expenses/${id}/items/${itemId}/match`)).data,
    onSuccess: invalidate,
    onError: (e) => toast.error(apiError(e)),
  });

  if (isLoading || !r) return <div className="text-slate-400">Loading…</div>;

  const isApprover = !!user?.is_expense_approver;
  const isOwnerOfReport = r.employee_id === user?.id;
  const isDraft = r.status === "draft";
  const canSubmit = isOwnerOfReport && isDraft;
  // Segregation of duties: an approver can't decide on their own report.
  const canDecide = isApprover && !isOwnerOfReport && (r.status === "submitted" || r.status === "approved");
  const verifiedCount = r.items.filter((it) => it.verified).length;

  // Compliance: every entry needs a business purpose AND an attached receipt.
  const itemMissing = (it: ExpenseReportDetail["items"][number]) => {
    const m: string[] = [];
    if (!it.comment || !it.comment.trim()) m.push("business purpose");
    if (!it.has_receipt) m.push("receipt");
    return m;
  };
  const incompleteCount = r.items.filter((it) => itemMissing(it).length > 0).length;
  const readyToSubmit = r.items.length > 0 && incompleteCount === 0;

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
          <Field label="Verified vs bank" value={`${verifiedCount} / ${r.items.length}`} />
          {r.total_eur && r.currency !== "EUR" && <Field label="Total (EUR, ECB)" value={money(r.total_eur)} />}
          {r.decided_by && <Field label={r.status} value={`${r.decided_by}`} />}
        </dl>
        {!isDraft && verifiedCount < r.items.length && (
          <div className="mt-2 text-xs text-amber-600">
            {r.items.length - verifiedCount} item(s) are not reconciled against a bank statement.
          </div>
        )}

        {canSubmit && incompleteCount > 0 && (
          <div className="mt-4 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-700">
            {incompleteCount} of {r.items.length} expense{r.items.length === 1 ? "" : "s"} still
            {" "}need a business purpose and/or a receipt before you can submit.
          </div>
        )}

        <div className="mt-4 flex flex-wrap gap-2">
          {canSubmit && (
            <button
              className="btn-primary disabled:opacity-50"
              disabled={!readyToSubmit || act.isPending}
              title={readyToSubmit ? "" : "Add a business purpose and receipt to every expense first"}
              onClick={() => act.mutate({ path: "submit" })}
            >
              Submit for approval
            </button>
          )}
          {canSubmit && <button className="btn-ghost text-rose-600" onClick={() => del.mutate()}>Delete draft</button>}
          {canDecide && r.status === "submitted" && (
            <>
              <button className="btn bg-emerald-600 text-white hover:bg-emerald-700" onClick={() => act.mutate({ path: "decision", body: { action: "approve" } })}>Approve</button>
              <button className="btn border border-rose-300 bg-white text-rose-600 hover:bg-rose-50" onClick={() => act.mutate({ path: "decision", body: { action: "reject" } })}>Reject</button>
            </>
          )}
          {canDecide && r.status === "approved" && (
            <button className="btn bg-emerald-600 text-white hover:bg-emerald-700" onClick={() => act.mutate({ path: "decision", body: { action: "reimburse" } })}>Mark reimbursed</button>
          )}
          {isApprover && isOwnerOfReport && r.status === "submitted" && (
            <span className="self-center text-xs text-slate-400">Another approver must review your own report.</span>
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
              <th className="px-4 py-3">Business purpose</th>
              <th className="px-4 py-3 text-right">VAT</th>
              <th className="px-4 py-3 text-right">Amount</th>
              <th className="px-4 py-3">Bank</th>
              <th className="px-4 py-3">Receipt</th>
            </tr>
          </thead>
          <tbody>
            {r.items.map((it) => {
              const missing = itemMissing(it);
              return (
              <tr key={it.id} className="border-b border-slate-100 last:border-0 align-top">
                <td className="px-4 py-3 text-slate-500">{shortDate(it.spend_date)}</td>
                <td className="px-4 py-3"><span className="badge bg-slate-100 text-slate-600">{it.category}</span></td>
                <td className="px-4 py-3">
                  {it.description}
                  {it.merchant && <div className="text-xs text-slate-400">{it.merchant}</div>}
                </td>
                <td className="px-4 py-3 min-w-[220px]">
                  {canSubmit ? (
                    <BusinessPurpose
                      value={it.comment ?? ""}
                      onSave={(v) => patchItem.mutate({ itemId: it.id, comment: v })}
                    />
                  ) : it.comment ? (
                    <span className="text-slate-600">{it.comment}</span>
                  ) : (
                    <span className="text-rose-500">— missing —</span>
                  )}
                </td>
                <td className="px-4 py-3 text-right text-slate-500">{money(it.vat_amount, r.currency)}</td>
                <td className="px-4 py-3 text-right font-medium">{money(it.amount, r.currency)}</td>
                <td className="px-4 py-3 min-w-[180px]">
                  {it.verified ? (
                    <div>
                      <span className="badge bg-emerald-100 text-emerald-700">✓ verified</span>
                      {it.bank_reference && <div className="mt-0.5 text-xs text-slate-400">{it.bank_reference}</div>}
                      {canSubmit && (
                        <button className="mt-0.5 block text-xs text-slate-400 hover:underline" onClick={() => unmatch.mutate(it.id)}>unmatch</button>
                      )}
                    </div>
                  ) : canSubmit ? (
                    <BankMatch
                      reportId={id!}
                      item={it}
                      onMatch={(txnId) => match.mutate({ itemId: it.id, transactionId: txnId })}
                    />
                  ) : (
                    <span className="text-amber-500">unverified</span>
                  )}
                </td>
                <td className="px-4 py-3">
                  {it.has_receipt ? (
                    <button className="text-brand-600 hover:underline" onClick={() => downloadFile(`/expenses/${id}/items/${it.id}/receipt`, `receipt-${it.id}`)}>view</button>
                  ) : canSubmit ? (
                    <button className="font-medium text-rose-600 hover:underline" onClick={() => { uploadItemId.current = it.id; fileRef.current?.click(); }}>attach</button>
                  ) : (
                    <span className="text-rose-400">missing</span>
                  )}
                  {canSubmit && missing.length > 0 && (
                    <div className="mt-1 text-xs text-amber-600">needs {missing.join(" + ")}</div>
                  )}
                </td>
              </tr>
            );})}
          </tbody>
        </table>
      </div>

      <CommentThread reportId={id!} />
    </div>
  );
}

// Reconcile a draft item against a bank/card statement line. Fetches candidate
// transactions (same amount) on demand and lets the employee pick one.
function BankMatch({ reportId, item, onMatch }:
  { reportId: string; item: ExpenseReportDetail["items"][number]; onMatch: (txnId: string) => void }) {
  const [open, setOpen] = useState(false);
  const candidates = useQuery<ExpenseTransaction[]>({
    queryKey: ["match-candidates", reportId, item.id],
    queryFn: async () => (await api.get(`/expenses/${reportId}/items/${item.id}/match-candidates`)).data,
    enabled: open,
  });

  if (!open) {
    return <button className="text-brand-600 hover:underline" onClick={() => setOpen(true)}>match to bank</button>;
  }
  return (
    <div className="space-y-1">
      {candidates.isLoading && <span className="text-xs text-slate-400">Searching…</span>}
      {candidates.data?.length === 0 && (
        <span className="text-xs text-amber-600">No matching statement line.</span>
      )}
      {candidates.data?.map((t) => (
        <button
          key={t.id}
          className="block w-full rounded border border-slate-200 px-2 py-1 text-left text-xs hover:bg-slate-50"
          onClick={() => onMatch(t.id)}
        >
          {shortDate(t.txn_date)} · {t.merchant || t.description} · {money(t.amount, t.currency)}
        </button>
      ))}
      <button className="text-xs text-slate-400 hover:underline" onClick={() => setOpen(false)}>cancel</button>
    </div>
  );
}

// Inline-editable business purpose for a draft expense item. Saves on blur/Enter.
function BusinessPurpose({ value, onSave }: { value: string; onSave: (v: string) => void }) {
  const [text, setText] = useState(value);
  const dirty = text.trim() !== value.trim();
  return (
    <input
      className={`input py-1 text-sm ${!value.trim() ? "border-rose-300 bg-rose-50 placeholder:text-rose-400" : ""}`}
      placeholder="Why was this spent? (required)"
      value={text}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => dirty && onSave(text.trim())}
      onKeyDown={(e) => { if (e.key === "Enter") { (e.target as HTMLInputElement).blur(); } }}
    />
  );
}

function CommentThread({ reportId }: { reportId: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [body, setBody] = useState("");
  const comments = useQuery<ExpenseComment[]>({
    queryKey: ["expense", reportId, "comments"],
    queryFn: async () => (await api.get(`/expenses/${reportId}/comments`)).data,
  });
  const post = useMutation({
    mutationFn: async () => (await api.post(`/expenses/${reportId}/comments`, { body })).data,
    onSuccess: () => { setBody(""); qc.invalidateQueries({ queryKey: ["expense", reportId, "comments"] }); },
    onError: (e) => toast.error(apiError(e)),
  });

  return (
    <div className="card space-y-3">
      <h2 className="text-sm font-semibold text-slate-600">Comments</h2>
      <div className="space-y-3">
        {comments.data?.length === 0 && <p className="text-sm text-slate-400">No comments yet.</p>}
        {comments.data?.map((c) => (
          <div key={c.id} className="flex gap-3">
            <div className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-brand-100 text-xs font-semibold text-brand-700">
              {c.author_name.slice(0, 2).toUpperCase()}
            </div>
            <div className="flex-1 rounded-lg bg-slate-50 px-3 py-2">
              <div className="text-xs text-slate-400">{c.author_name} · {shortDate(c.created_at)}</div>
              <div className="text-sm text-slate-700 whitespace-pre-wrap">{c.body}</div>
            </div>
          </div>
        ))}
      </div>
      <div className="flex gap-2">
        <input
          className="input flex-1"
          placeholder="Add a comment…"
          value={body}
          onChange={(e) => setBody(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && body.trim()) post.mutate(); }}
        />
        <button className="btn-primary" disabled={!body.trim() || post.isPending} onClick={() => post.mutate()}>Post</button>
      </div>
    </div>
  );
}
