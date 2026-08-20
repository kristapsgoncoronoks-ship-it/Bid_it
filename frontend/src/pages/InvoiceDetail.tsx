import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { DeleteInvoiceButton } from "../components/DeleteInvoiceButton";
import { Field } from "../components/Field";
import { Link, useParams } from "react-router-dom";
import { PageHeader, QueryState, Skeleton } from "../components/ui";
import { api, apiError } from "../lib/api";
import {
  money,
  SEVERITY_STYLES,
  shortDate,
  STATUS_STYLES,
  VALIDATION_LABELS,
  VALIDATION_STYLES,
} from "../lib/format";
import {
  type CostMaster,
  DIMENSION_LABELS,
  type Dimensions,
  type InvoiceAllocation,
  type InvoiceDetail,
  type InvoiceStatus,
  type SupplierPayment,
} from "../lib/types";

const PAY_STATUS_STYLE: Record<string, string> = {
  paid: "bg-emerald-100 text-emerald-700",
  partial: "bg-sky-100 text-sky-700",
  open: "bg-slate-100 text-slate-600",
  overdue: "bg-rose-100 text-rose-700",
};

const STATUSES: InvoiceStatus[] = ["draft", "pending", "paid", "overdue"];

export default function InvoiceDetailPage() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();

  const query = useQuery<InvoiceDetail>({
    queryKey: ["invoice", id],
    queryFn: async () => (await api.get(`/invoices/${id}`)).data,
    enabled: !!id,
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["invoice", id] });
    qc.invalidateQueries({ queryKey: ["invoices"] });
    qc.invalidateQueries({ queryKey: ["analytics"] });
  };

  const setStatus = useMutation({
    mutationFn: async (status: InvoiceStatus) =>
      (await api.patch(`/invoices/${id}`, { status })).data,
    onSuccess: invalidate,
  });

  const decide = useMutation({
    mutationFn: async (action: "approve" | "reject") =>
      (await api.post(`/invoices/${id}/validate`, { action })).data,
    onSuccess: invalidate,
  });

  return (
    <div className="space-y-6">
      <Link to="/invoices" className="text-sm text-brand-600 hover:underline">
        ← Back to invoices
      </Link>

      <QueryState
        query={query}
        loading={<Skeleton className="h-40 w-full" />}
        errorTitle="Couldn’t load this invoice"
      >
        {(inv) => (
          <div className="space-y-6">
            <PageHeader
              title={inv.invoice_number}
              description={
                <>
                  {inv.vendor_name}
                  {" · "}
                  <Link to={`/invoices/${id}/review`} className="text-brand-600 hover:underline">
                    Open review &amp; approval workspace →
                  </Link>
                </>
              }
              actions={
                <div className="text-right">
                  <div className="text-2xl font-semibold">{money(inv.total, inv.currency)}</div>
                  <span className={`badge mt-1 ${STATUS_STYLES[inv.status] ?? ""}`}>{inv.status}</span>
                  {/* The SPA had no way to delete an invoice at all, which made
                      the recycle bin unreachable in practice. The consent gate
                      lives in the button, sourced from the server's 409. */}
                  <div className="mt-3">
                    <DeleteInvoiceButton invoiceId={id!} />
                  </div>
                </div>
              }
            />

            <div className="card">
              <dl className="grid grid-cols-2 gap-4 text-sm md:grid-cols-4">
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

            <APPayment inv={inv} onPaid={invalidate} />

            <CostAllocation inv={inv} onSaved={invalidate} />

            {inv.validation_status !== "none" && (
              <div className="card space-y-3">
                <div className="flex items-center justify-between">
                  <h2 className="text-sm font-semibold text-slate-600">Validation</h2>
                  <span className={`badge ${VALIDATION_STYLES[inv.validation_status] ?? ""}`}>
                    {VALIDATION_LABELS[inv.validation_status] ?? inv.validation_status}
                  </span>
                </div>

                {inv.validation_findings.length > 0 ? (
                  <ul className="space-y-1.5">
                    {inv.validation_findings.map((f, i) => (
                      <li key={i} className="flex items-start gap-2 text-sm">
                        <span className={`badge ${SEVERITY_STYLES[f.severity] ?? ""}`}>{f.severity}</span>
                        <span className="text-slate-600">{f.message}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-sm text-slate-400">No issues detected by the automated checks.</p>
                )}

                {(inv.validation_status === "pending" || inv.validation_status === "flagged") && (
                  <div className="flex gap-2 pt-1">
                    <button
                      className="btn bg-emerald-600 text-white hover:bg-emerald-700"
                      disabled={decide.isPending}
                      onClick={() => decide.mutate("approve")}
                    >
                      Approve
                    </button>
                    <button
                      className="btn border border-rose-300 bg-white text-rose-600 hover:bg-rose-50"
                      disabled={decide.isPending}
                      onClick={() => decide.mutate("reject")}
                    >
                      Reject
                    </button>
                  </div>
                )}
                {inv.validated_by && (
                  <p className="text-xs text-slate-400">
                    {inv.validation_status} by {inv.validated_by}
                    {inv.validated_at && ` · ${shortDate(inv.validated_at)}`}
                  </p>
                )}
              </div>
            )}

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

            <ProjectAllocation invoiceId={inv.id} onSaved={invalidate} />
          </div>
        )}
      </QueryState>
    </div>
  );
}

// Editable cost-allocation tags (cost center, department, project, vehicle,
// property). Saved via PATCH; only changed fields are sent.
function APPayment({ inv, onPaid }: { inv: InvoiceDetail; onPaid: () => void }) {
  const payable =
    inv.workflow_state === "scheduled_for_payment" || inv.workflow_state === "partially_paid";
  const [amount, setAmount] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const ledger = useQuery<SupplierPayment[]>({
    queryKey: ["invoice", inv.id, "payments"],
    queryFn: async () => (await api.get(`/invoices/${inv.id}/payments`)).data,
  });

  const pay = useMutation({
    mutationFn: async (cumulative: string) =>
      (await api.patch(`/invoices/${inv.id}/payment`, { amount_paid: cumulative })).data,
    onSuccess: () => {
      setAmount("");
      setErr(null);
      onPaid();
      ledger.refetch();
    },
    onError: (e) => setErr(apiError(e)),
  });

  // The record form adds to the already-paid total (the API takes the new cumulative).
  const submit = () => {
    const add = Number(amount);
    if (!(add > 0)) return;
    pay.mutate((Number(inv.amount_paid) + add).toFixed(2));
  };

  const inputClass = "rounded-lg border border-slate-300 px-2 py-1 text-sm";
  return (
    <div className="card space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-600">Payment</h2>
        <span className={`badge ${PAY_STATUS_STYLE[inv.payment_status] ?? ""}`}>
          {inv.payment_status}
        </span>
      </div>
      <dl className="grid grid-cols-3 gap-4 text-sm">
        <Field label="Paid" value={money(inv.amount_paid, inv.currency)} />
        <Field label="Outstanding" value={money(inv.outstanding, inv.currency)} />
        <Field label="Paid date" value={shortDate(inv.paid_date)} />
      </dl>

      {err && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
          {err}
        </div>
      )}

      {payable ? (
        <div className="flex items-end gap-2">
          <div>
            <label className="label">Record a payment ({inv.currency})</label>
            <input
              className={`${inputClass} w-40`}
              inputMode="decimal"
              placeholder="0.00"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
            />
          </div>
          <button
            className="btn bg-brand-500 text-white hover:bg-brand-600"
            disabled={pay.isPending || !(Number(amount) > 0)}
            onClick={submit}
          >
            Record payment
          </button>
        </div>
      ) : (
        <p className="text-xs text-slate-400">
          An invoice can be paid once it is approved and scheduled for payment.
        </p>
      )}

      {(ledger.data ?? []).length > 0 && (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-slate-400">
              <th className="py-1">Date</th>
              <th className="py-1">Method</th>
              <th className="py-1">Reference</th>
              <th className="py-1 text-right">Amount</th>
            </tr>
          </thead>
          <tbody>
            {(ledger.data ?? []).map((p) => (
              <tr key={p.id} className="border-t border-slate-100">
                <td className="py-1 text-slate-500">{shortDate(p.paid_on)}</td>
                <td className="py-1 text-slate-500">{p.method}</td>
                <td className="py-1 text-slate-500">{p.reference ?? "—"}</td>
                <td
                  className={`py-1 text-right tabular-nums ${Number(p.amount) < 0 ? "text-rose-600" : ""}`}
                >
                  {money(p.amount, inv.currency)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function CostAllocation({ inv, onSaved }: { inv: InvoiceDetail; onSaved: () => void }) {
  const keys = Object.keys(DIMENSION_LABELS) as (keyof Dimensions)[];
  const [draft, setDraft] = useState<Record<string, string>>(
    Object.fromEntries(keys.map((k) => [k, inv[k] ?? ""])),
  );

  const dirty = keys.filter((k) => (draft[k] ?? "") !== (inv[k] ?? ""));
  const save = useMutation({
    mutationFn: async () => {
      const patch: Record<string, string | null> = {};
      for (const k of dirty) patch[k] = draft[k].trim() === "" ? null : draft[k].trim();
      return (await api.patch(`/invoices/${inv.id}`, patch)).data;
    },
    onSuccess: onSaved,
  });

  return (
    <div className="card space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-slate-600">Cost allocation</h2>
          <p className="text-xs text-slate-400">Tag this invoice to a cost object for reporting. Leave blank if not applicable.</p>
        </div>
        <button
          className="btn-primary"
          disabled={dirty.length === 0 || save.isPending}
          onClick={() => save.mutate()}
        >
          {save.isPending ? "Saving…" : dirty.length ? `Save (${dirty.length})` : "Saved"}
        </button>
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {keys.map((k) => (
          <div key={k}>
            <label className="label">{DIMENSION_LABELS[k]}</label>
            <input
              className="input"
              value={draft[k]}
              placeholder="—"
              onChange={(e) => setDraft({ ...draft, [k]: e.target.value })}
            />
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * Project allocation (project-profitability phase 2). One card, one write:
 * the whole-invoice project, the percentage split, and per-line tags travel in
 * a single PUT so the three levels can never contradict each other. The GET's
 * body IS a valid PUT body — the editor round-trips without translation.
 *
 * Percent rows must sum to exactly 100 (mirrored client-side only as guidance;
 * the server is the control and refuses anything else).
 */
function ProjectAllocation({ invoiceId, onSaved }: { invoiceId: string; onSaved: () => void }) {
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [projectId, setProjectId] = useState<string>("");
  const [splits, setSplits] = useState<{ project_id: string; percent: string }[]>([]);
  const [loaded, setLoaded] = useState(false);

  const projects = useQuery<CostMaster[]>({
    queryKey: ["masters", "projects"],
    queryFn: async () => (await api.get("/masters/projects")).data,
  });
  const allocation = useQuery<InvoiceAllocation>({
    queryKey: ["invoice", invoiceId, "allocation"],
    queryFn: async () => (await api.get(`/invoices/${invoiceId}/allocation`)).data,
  });

  if (allocation.data && !loaded) {
    setProjectId(allocation.data.project_id ?? "");
    setSplits(allocation.data.splits);
    setLoaded(true);
  }

  const save = useMutation({
    mutationFn: async () =>
      (
        await api.put(`/invoices/${invoiceId}/allocation`, {
          project_id: projectId || null,
          splits,
        })
      ).data,
    onSuccess: () => {
      setErr(null);
      setSaved(true);
      onSaved();
    },
    onError: (e) => {
      setSaved(false);
      setErr(apiError(e));
    },
  });

  // Hidden until the org uses projects at all — a picker over an empty master
  // is noise on every invoice for clients who never open projects.
  if ((projects.data?.length ?? 0) === 0) return null;

  const active = projects.data!.filter((m) => m.status !== "archived");
  const pctSum = splits.reduce((t, s) => t + Number(s.percent || 0), 0);

  return (
    <div className="card space-y-3">
      <div>
        <h2 className="text-sm font-semibold text-slate-600">Project allocation</h2>
        <p className="text-xs text-slate-400">
          Books this invoice&apos;s cost onto a project — one project, or split by
          percentage when one invoice covers several jobs.
        </p>
      </div>
      {err && (
        <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">
          {err}
        </div>
      )}
      <div className="flex flex-wrap items-end gap-2">
        <div>
          <label className="label">Project</label>
          <select
            className="input"
            value={projectId}
            onChange={(e) => {
              setProjectId(e.target.value);
              setSaved(false);
            }}
          >
            <option value="">— None —</option>
            {active.map((m) => (
              <option key={m.id} value={m.id}>
                {m.code} · {m.name}
              </option>
            ))}
          </select>
        </div>
        <button
          className="btn-ghost text-xs"
          onClick={() => {
            setSplits([...splits, { project_id: active[0]?.id ?? "", percent: "" }]);
            setSaved(false);
          }}
        >
          + Split by %
        </button>
      </div>
      {splits.length > 0 && (
        <div className="space-y-2">
          {splits.map((s, i) => (
            <div key={i} className="flex items-center gap-2">
              <select
                className="input"
                value={s.project_id}
                onChange={(e) => {
                  setSplits(splits.map((x, j) => (j === i ? { ...x, project_id: e.target.value } : x)));
                  setSaved(false);
                }}
              >
                {active.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.code} · {m.name}
                  </option>
                ))}
              </select>
              <input
                className="input w-24 text-right"
                placeholder="%"
                value={s.percent}
                onChange={(e) => {
                  setSplits(splits.map((x, j) => (j === i ? { ...x, percent: e.target.value } : x)));
                  setSaved(false);
                }}
              />
              <button
                className="btn-ghost text-xs text-rose-500"
                onClick={() => {
                  setSplits(splits.filter((_, j) => j !== i));
                  setSaved(false);
                }}
              >
                remove
              </button>
            </div>
          ))}
          <p className={`text-xs ${pctSum === 100 ? "text-slate-400" : "text-amber-600"}`}>
            Split total: {pctSum}% {pctSum !== 100 && "— must be exactly 100%"}
          </p>
        </div>
      )}
      <div className="flex items-center gap-2">
        <button
          className="btn-primary"
          disabled={save.isPending || (splits.length > 0 && pctSum !== 100)}
          onClick={() => save.mutate()}
        >
          Save allocation
        </button>
        {saved && <span className="text-xs text-emerald-600">Saved.</span>}
      </div>
    </div>
  );
}
