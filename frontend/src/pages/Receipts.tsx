import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Button, Card, EmptyState, PageHeader, QueryState, Skeleton, type Tone } from "../components/ui";
import { api, apiError } from "../lib/api";
import { money, shortDate } from "../lib/format";
import type { IssuedInvoice, Paginated, Receipt, ReceiptDetail } from "../lib/types";

function unallocatedTone(r: Receipt): Tone {
  const free = Number(r.unallocated);
  if (free <= 0) return "success"; // fully applied
  if (Number(r.allocated) > 0) return "info"; // partly applied
  return "neutral"; // nothing applied yet
}

function unallocatedLabel(r: Receipt): string {
  const free = Number(r.unallocated);
  if (free <= 0) return "Fully applied";
  if (Number(r.allocated) > 0) return `${money(r.unallocated, "EUR")} unapplied`;
  return "Unapplied";
}

export default function ReceiptsPage() {
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);

  const receipts = useQuery<Receipt[]>({
    queryKey: ["receipts"],
    queryFn: async () => (await api.get("/receipts")).data,
  });

  const invalidate = () => {
    setErr(null);
    qc.invalidateQueries({ queryKey: ["receipts"] });
    qc.invalidateQueries({ queryKey: ["receipts", openId] });
    qc.invalidateQueries({ queryKey: ["issued"] });
  };
  const onErr = (e: unknown) => setErr(apiError(e));

  return (
    <div className="space-y-6">
      <PageHeader
        title="Receipts"
        description="Record money received and apply it across your customer invoices."
        actions={
          <Link to="/issue" className="text-sm text-brand-600 hover:underline">
            ← Customer invoices
          </Link>
        }
      />

      {err && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-700">
          {err}
        </div>
      )}

      <NewReceipt onCreated={invalidate} onErr={onErr} />

      <Card>
        <h2 className="mb-3 text-sm font-semibold text-slate-700">Received</h2>
        <QueryState
          query={receipts}
          loading={<Skeleton className="h-24 w-full" />}
          isEmpty={(d) => d.length === 0}
          empty={<EmptyState title="No receipts recorded yet" />}
          errorTitle="Couldn’t load receipts"
        >
          {(d) => (
            <div className="space-y-3">
              {d.map((r) => (
                <div key={r.id} className="rounded-lg border border-slate-200 p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <Badge tone={unallocatedTone(r)}>{unallocatedLabel(r)}</Badge>
                      <span className="font-medium tabular-nums">{money(r.amount, "EUR")}</span>
                      <span className="text-xs text-slate-400">
                        {shortDate(r.received_on)} · {r.method}
                        {r.reference ? ` · ${r.reference}` : ""}
                      </span>
                    </div>
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => setOpenId(openId === r.id ? null : r.id)}
                    >
                      {openId === r.id ? "Hide" : "Apply / details"}
                    </Button>
                  </div>
                  {openId === r.id && (
                    <ReceiptDetailPanel receiptId={r.id} onChanged={invalidate} onErr={onErr} />
                  )}
                </div>
              ))}
            </div>
          )}
        </QueryState>
      </Card>
    </div>
  );
}

function NewReceipt({ onCreated, onErr }: { onCreated: () => void; onErr: (e: unknown) => void }) {
  const today = new Date().toISOString().slice(0, 10);
  const [amount, setAmount] = useState("");
  const [receivedOn, setReceivedOn] = useState(today);
  const [reference, setReference] = useState("");

  const create = useMutation({
    mutationFn: async () =>
      (
        await api.post("/receipts", {
          amount,
          received_on: receivedOn,
          method: "bank_transfer",
          reference: reference || null,
        })
      ).data,
    onSuccess: () => {
      setAmount("");
      setReference("");
      onCreated();
    },
    onError: onErr,
  });

  const inputClass = "rounded-lg border border-slate-300 px-2 py-1 text-sm";
  return (
    <Card>
      <h2 className="mb-3 text-sm font-semibold text-slate-700">Record a receipt</h2>
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-xs text-slate-500">
          Amount (EUR)
          <input
            className={`mt-1 block w-32 ${inputClass}`}
            inputMode="decimal"
            placeholder="0.00"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
          />
        </label>
        <label className="text-xs text-slate-500">
          Received on
          <input
            type="date"
            className={`mt-1 block ${inputClass}`}
            value={receivedOn}
            onChange={(e) => setReceivedOn(e.target.value)}
          />
        </label>
        <label className="text-xs text-slate-500">
          Reference
          <input
            className={`mt-1 block w-48 ${inputClass}`}
            placeholder="Bank ref…"
            value={reference}
            onChange={(e) => setReference(e.target.value)}
          />
        </label>
        <Button
          size="sm"
          disabled={!amount || Number(amount) <= 0}
          loading={create.isPending}
          onClick={() => create.mutate()}
        >
          Add receipt
        </Button>
      </div>
    </Card>
  );
}

function ReceiptDetailPanel({
  receiptId,
  onChanged,
  onErr,
}: {
  receiptId: string;
  onChanged: () => void;
  onErr: (e: unknown) => void;
}) {
  const qc = useQueryClient();
  const detail = useQuery<ReceiptDetail>({
    queryKey: ["receipts", receiptId],
    queryFn: async () => (await api.get(`/receipts/${receiptId}`)).data,
  });
  // Open invoices to apply against (positive outstanding, not credit notes/void).
  const invoices = useQuery<Paginated<IssuedInvoice>>({
    queryKey: ["issued"],
    queryFn: async () => (await api.get("/issued")).data,
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["receipts", receiptId] });
    onChanged();
  };

  const [invoiceId, setInvoiceId] = useState("");
  const [amount, setAmount] = useState("");

  const allocate = useMutation({
    mutationFn: async () =>
      (await api.post(`/receipts/${receiptId}/allocate`, { invoice_id: invoiceId, amount })).data,
    onSuccess: () => {
      setInvoiceId("");
      setAmount("");
      refresh();
    },
    onError: onErr,
  });
  const reverse = useMutation({
    mutationFn: async (paymentId: string) =>
      (await api.post(`/receipts/${receiptId}/deallocate`, { payment_id: paymentId })).data,
    onSuccess: refresh,
    onError: onErr,
  });

  const open = (invoices.data?.items ?? []).filter(
    (i) => i.doc_type !== "credit_note" && i.status !== "void" && Number(i.outstanding) > 0,
  );
  const inputClass = "rounded-lg border border-slate-300 px-2 py-1 text-sm";

  return (
    <div className="mt-3 border-t border-slate-100 pt-3">
      <QueryState
        query={detail}
        loading={<Skeleton className="h-16 w-full" />}
        errorTitle="Couldn’t load this receipt's allocations"
      >
        {(d) => {
          const free = Number(d.unallocated ?? 0);
          // A reversal is itself a negative ledger row noted "reversal:<id>".
          // Only a positive allocation that has not been reversed can be reversed.
          const allocations = d.allocations ?? [];
          const reversedIds = new Set(
            allocations.filter((a) => a.note?.startsWith("reversal:")).map((a) => a.note!.slice(9)),
          );
          return (
            <>
      {/* Existing allocations */}
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-slate-400">
            <th className="py-1">Invoice</th>
            <th className="py-1">Applied</th>
            <th className="py-1 text-right">Amount</th>
            <th className="py-1"></th>
          </tr>
        </thead>
        <tbody>
          {allocations.map((a) => {
            const isReversal = a.note?.startsWith("reversal:");
            const reversed = reversedIds.has(a.id);
            return (
              <tr key={a.id} className="border-t border-slate-100">
                <td className="py-1 font-mono text-xs">{a.issued_invoice_id.slice(0, 8)}</td>
                <td className="py-1 text-slate-500">{shortDate(a.paid_on)}</td>
                <td
                  className={`py-1 text-right tabular-nums ${
                    Number(a.amount) < 0 ? "text-rose-600" : ""
                  }`}
                >
                  {money(a.amount, "EUR")}
                </td>
                <td className="py-1 text-right">
                  {!isReversal && !reversed && Number(a.amount) > 0 && (
                    <Button
                      size="sm"
                      variant="ghost"
                      loading={reverse.isPending}
                      onClick={() => reverse.mutate(a.id)}
                    >
                      Reverse
                    </Button>
                  )}
                  {reversed && <span className="text-xs text-slate-400">reversed</span>}
                </td>
              </tr>
            );
          })}
          {allocations.length === 0 && (
            <tr>
              <td colSpan={4} className="py-2 text-slate-400">
                Nothing applied yet.
              </td>
            </tr>
          )}
        </tbody>
      </table>

      {/* Apply the unallocated balance to an open invoice */}
      {free > 0 && (
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <label className="text-xs text-slate-500">
            Apply to
            <select
              className={`mt-1 block w-64 ${inputClass}`}
              value={invoiceId}
              onChange={(e) => {
                const inv = open.find((o) => o.id === e.target.value);
                setInvoiceId(e.target.value);
                // Default to the smaller of the free balance and what the invoice owes.
                if (inv) setAmount(String(Math.min(free, Number(inv.outstanding)).toFixed(2)));
              }}
            >
              <option value="">Select an invoice…</option>
              {open.map((i) => (
                <option key={i.id} value={i.id}>
                  {i.number} · {i.buyer_name} · {money(i.outstanding, i.currency)} due
                </option>
              ))}
            </select>
          </label>
          <label className="text-xs text-slate-500">
            Amount (EUR)
            <input
              className={`mt-1 block w-28 ${inputClass}`}
              inputMode="decimal"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
            />
          </label>
          <Button
            size="sm"
            disabled={!invoiceId || !amount || Number(amount) <= 0}
            loading={allocate.isPending}
            onClick={() => allocate.mutate()}
          >
            Apply {money(d.unallocated ?? 0, "EUR")} left
          </Button>
        </div>
      )}
      {free <= 0 && allocations.length > 0 && (
        <p className="mt-2 text-xs text-slate-400">This receipt is fully applied.</p>
      )}
            </>
          );
        }}
      </QueryState>
    </div>
  );
}
