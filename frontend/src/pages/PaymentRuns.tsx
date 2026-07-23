import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Button, Card, type Tone } from "../components/ui";
import { api, apiError, downloadFile } from "../lib/api";
import { money, shortDate } from "../lib/format";
import type { PaymentRun, RunInvoice } from "../lib/types";

const STATUS_TONE: Record<PaymentRun["status"], Tone> = {
  open: "info",
  paid: "success",
  cancelled: "neutral",
};

export default function PaymentRunsPage() {
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const [refs, setRefs] = useState<Record<string, string>>({});

  const payable = useQuery<RunInvoice[]>({
    queryKey: ["payment-runs", "payable"],
    queryFn: async () => (await api.get("/payment-runs/payable")).data,
  });
  const runs = useQuery<PaymentRun[]>({
    queryKey: ["payment-runs"],
    queryFn: async () => (await api.get("/payment-runs")).data,
  });

  const invalidate = () => {
    setErr(null);
    qc.invalidateQueries({ queryKey: ["payment-runs"] });
  };
  const onErr = (e: unknown) => setErr(apiError(e));

  const create = useMutation({
    mutationFn: async () =>
      (
        await api.post("/payment-runs", {
          invoice_ids: Object.keys(picked).filter((k) => picked[k]),
          method: "bank_transfer",
        })
      ).data,
    onSuccess: () => {
      setPicked({});
      invalidate();
    },
    onError: onErr,
  });
  const pay = useMutation({
    mutationFn: async (r: PaymentRun) =>
      (await api.post(`/payment-runs/${r.id}/pay`, { version: r.version, reference: refs[r.id] || null }))
        .data,
    onSuccess: invalidate,
    onError: onErr,
  });
  const cancel = useMutation({
    mutationFn: async (id: string) => (await api.delete(`/payment-runs/${id}`)).data,
    onSuccess: invalidate,
    onError: onErr,
  });

  const items = payable.data ?? [];
  const selectedTotal = items
    .filter((i) => picked[i.id])
    .reduce((s, i) => s + Number(i.total_eur ?? i.total), 0);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Payment runs</h1>
          <p className="text-slate-500">Pay approved, scheduled supplier invoices in a batch.</p>
        </div>
        <Link to="/invoices" className="text-sm text-brand-600 hover:underline">
          ← Invoices
        </Link>
      </div>

      {err && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-700">
          {err}
        </div>
      )}

      {/* Scheduled invoices → new run */}
      <Card>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-700">Scheduled — awaiting payment</h2>
          <Button
            size="sm"
            disabled={selectedTotal === 0}
            loading={create.isPending}
            onClick={() => create.mutate()}
          >
            Create run ({money(selectedTotal, "EUR")})
          </Button>
        </div>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-slate-400">
              <th className="py-1"></th>
              <th className="py-1">Invoice</th>
              <th className="py-1">Supplier</th>
              <th className="py-1 text-right">Amount</th>
            </tr>
          </thead>
          <tbody>
            {items.map((i) => (
              <tr key={i.id} className="border-t border-slate-100">
                <td className="py-1">
                  <input
                    type="checkbox"
                    checked={!!picked[i.id]}
                    onChange={(e) => setPicked({ ...picked, [i.id]: e.target.checked })}
                    aria-label={`Select ${i.invoice_number}`}
                  />
                </td>
                <td className="py-1">{i.invoice_number}</td>
                <td className="py-1 text-slate-500">{i.vendor_name ?? "—"}</td>
                <td className="py-1 text-right tabular-nums">{money(i.total, i.currency)}</td>
              </tr>
            ))}
            {items.length === 0 && (
              <tr>
                <td colSpan={4} className="py-3 text-slate-400">
                  No scheduled invoices awaiting payment.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Card>

      {/* Existing runs */}
      <Card>
        <h2 className="mb-3 text-sm font-semibold text-slate-700">Runs</h2>
        <div className="space-y-3">
          {(runs.data ?? []).map((r) => (
            <div key={r.id} className="rounded-lg border border-slate-200 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <Badge tone={STATUS_TONE[r.status]}>{r.status}</Badge>
                  <span className="font-medium">{money(r.total_eur, "EUR")}</span>
                  <span className="text-xs text-slate-400">
                    {r.invoice_count} invoice{r.invoice_count === 1 ? "" : "s"} · {r.method}
                    {r.reference ? ` · ${r.reference}` : ""}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  {r.status === "open" && (
                    <>
                      <input
                        className="w-40 rounded-lg border border-slate-300 px-2 py-1 text-sm"
                        placeholder="Payment reference…"
                        value={refs[r.id] ?? ""}
                        onChange={(e) => setRefs({ ...refs, [r.id]: e.target.value })}
                      />
                      <Button size="sm" loading={pay.isPending} onClick={() => pay.mutate(r)}>
                        Mark paid
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => cancel.mutate(r.id)}>
                        Cancel
                      </Button>
                    </>
                  )}
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() =>
                      downloadFile(`/payment-runs/${r.id}/export`, `payment-run-${r.reference || r.id}.csv`)
                    }
                  >
                    Export CSV
                  </Button>
                </div>
              </div>
              <div className="mt-1 text-xs text-slate-400">
                Created {shortDate(r.created_at)}
                {r.created_by ? ` by ${r.created_by}` : ""}
                {r.paid_at ? ` · paid ${shortDate(r.paid_at)}` : ""}
              </div>
            </div>
          ))}
          {(runs.data ?? []).length === 0 && (
            <p className="text-sm text-slate-400">No payment runs yet.</p>
          )}
        </div>
      </Card>
    </div>
  );
}
