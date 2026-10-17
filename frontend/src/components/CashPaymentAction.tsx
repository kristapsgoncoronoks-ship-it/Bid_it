import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { api, apiError } from "../lib/api";
import { checkSavedInvoiceCommand, hasSavedInvoiceCommand, invoiceCommand, retrySavedCashCommand } from "../lib/invoiceCommands";
import { money } from "../lib/format";
import type { IssuedInvoice } from "../lib/types";

type Entry = {
  id: string; amount: string; paid_on: string; method: string; reference: string | null;
  note: string | null; receipt_id: string | null; reversal_of_id: string | null;
};
type Result = { payment: Entry; invoice: IssuedInvoice };
type Props = { inv: IssuedInvoice; onDone: () => void | Promise<unknown> };

export function CashPaymentAction(props: Props) {
  const { user, org } = useAuth();
  // Remount local inputs when the account/company or invoice changes.
  return <CashPaymentControls key={`${user?.id}:${org?.id}:${props.inv.id}`} {...props} />;
}

function CashPaymentControls({ inv, onDone }: Props) {
  const { user, org, hasPerm } = useAuth();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [amount, setAmount] = useState("");
  const [paidOn, setPaidOn] = useState("");
  const [method, setMethod] = useState("bank_transfer");
  const [reference, setReference] = useState("");
  const [expected, setExpected] = useState(inv.outstanding);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [reversing, setReversing] = useState<Entry | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const scope = user && org ? { userId: user.id, orgId: org.id } : null;
  const storagePath = `/issued/${inv.id}/cash-command`;
  const lookup = (key: string) => `/issued/${inv.id}/payment-commands/${key}`;
  const canRead = hasPerm("payment.read");
  const canWrite = hasPerm("payment.write");
  const receivable = !inv.voided_at && ["issued", "disputed"].includes(inv.lifecycle);
  const saved = scope ? hasSavedInvoiceCommand(storagePath, scope) : false;
  const historyKey = ["issued-payments", user?.id, org?.id, inv.id];
  const history = useQuery<Entry[]>({ queryKey: historyKey, enabled: historyOpen && canRead,
    queryFn: async () => (await api.get(`/issued/${inv.id}/payments`)).data });
  const afterSuccess = async (result: Result) => {
    setOpen(false); setReversing(null); setReason(""); setError(null);
    setSuccess(result.payment.reversal_of_id ? "Explained reversal recorded." : "Payment recorded in the cash ledger; bank reconciliation is a separate step.");
    await Promise.all([onDone(), qc.invalidateQueries({ queryKey: historyKey })]);
  };
  const command = useMutation({ mutationFn: async ({ path, payload }: { path: string; payload: object }) => {
    if (!scope) throw new Error("Sign in before recording cash.");
    return invoiceCommand<Result>(path, payload, scope, lookup, undefined, storagePath, true);
  }, onSuccess: afterSuccess, onError: (e) => { setSuccess(null); setError(apiError(e)); } });
  const busy = command.isPending || checking;
  const check = async () => {
    if (!scope) return;
    setChecking(true); setSuccess(null);
    try {
      const result = await checkSavedInvoiceCommand<Result>(storagePath, scope, lookup);
      if (result) await afterSuccess(result);
      else setError("No committed payment was found. Keep the original inputs and retry the same request when connected.");
    } catch (e) { setError(apiError(e)); }
    finally { setChecking(false); }
  };
  const record = (value: string, balance: string, date: string, paymentMethod = "bank_transfer", paymentReference = "") => command.mutate({
    path: `/issued/${inv.id}/payments`,
    payload: { amount: value, currency: inv.currency, expected_outstanding: balance, paid_on: date || null,
      method: paymentMethod, reference: paymentReference || null },
  });
  const retry = async () => {
    if (!scope) return;
    setChecking(true); setSuccess(null);
    try { await afterSuccess(await retrySavedCashCommand<Result>(storagePath, scope, lookup)); }
    catch (e) { setError(apiError(e)); }
    finally { setChecking(false); }
  };
  const reversed = new Set((history.data ?? []).map((entry) => entry.reversal_of_id));
  return <div className="flex max-w-md flex-col items-end gap-2 text-xs">
    <div className="flex items-center gap-2">
      {inv.status === "paid" ? <span className="text-emerald-600">Paid {inv.paid_date ?? ""}</span> : <>
        <button className="text-emerald-600 hover:underline disabled:text-slate-400" disabled={!canWrite || !receivable || busy || saved}
          title={canWrite ? `Record the remaining ${money(inv.outstanding, inv.currency)} as a bank transfer; use Record for another method` : "Payment write permission is required"}
          onClick={() => record(inv.outstanding, inv.outstanding, "")}>Mark paid</button>
        <button className="text-brand-600 hover:underline disabled:text-slate-400" disabled={!canWrite || !receivable || busy || saved}
          title={canWrite ? "Record a positive additional payment; corrections use explained reversal" : "Payment write permission is required"}
          onClick={() => { setExpected(inv.outstanding); setOpen(true); setAmount(""); setPaidOn(""); setMethod("bank_transfer"); setReference(""); setSuccess(null); }}>Record…</button>
      </>}
      <button className="text-brand-600 hover:underline" disabled={!canRead || busy}
        title="Review cash entries and correct a direct payment through an explained reversal"
        onClick={() => setHistoryOpen(!historyOpen)}>{historyOpen ? "Hide payments" : "Payment history"}</button>
    </div>
    {open && <div className="flex flex-col items-end gap-1">
      <span>Additional payment in {inv.currency}; reviewed balance {money(expected, inv.currency)}.</span>
      <div className="flex flex-wrap justify-end gap-1">
        <input aria-label="Additional payment amount" className="input w-24 py-1" inputMode="decimal" value={amount} disabled={busy} onChange={(e) => setAmount(e.target.value)} />
        <input aria-label="Payment date" className="input w-32 py-1" type="date" value={paidOn} disabled={busy} onChange={(e) => setPaidOn(e.target.value)} />
        <select aria-label="Payment method" className="input w-32 py-1" value={method} disabled={busy} onChange={(e) => setMethod(e.target.value)}>
          <option value="bank_transfer">Bank transfer</option><option value="cash">Cash</option>
          <option value="card">Card</option><option value="other">Other</option>
        </select>
        <input aria-label="Payment reference" className="input w-36 py-1" maxLength={140} value={reference} disabled={busy} onChange={(e) => setReference(e.target.value)} placeholder="Reference (optional)" />
        <button className="btn-primary py-1 text-xs" disabled={!canWrite || busy || !amount || Number(amount) <= 0}
          title="Commit this positive payment once; retries retain its command identity"
          onClick={() => record(amount, expected, paidOn, method, reference)}>{command.isPending ? "Recording…" : "Record payment"}</button>
        <button disabled={busy} title="Close the editor; preserve any unresolved saved cash command" onClick={() => setOpen(false)}>Cancel</button>
      </div>
    </div>}
    {saved && <div className="flex gap-2">
      <button className="btn-ghost py-1 text-xs" disabled={busy || !canRead} title="Look up the saved payment command before recording another payment" onClick={check}>Check previous payment</button>
      <button className="btn-ghost py-1 text-xs" disabled={busy || !canWrite} title="Look up the committed result, then retry the original cash inputs with the same command identity if needed" onClick={retry}>Retry previous payment</button>
    </div>}
    {historyOpen && <div className="w-full rounded border border-slate-200 p-2 text-left">
      {history.isPending && <p>Loading payment history…</p>}
      {history.isError && <p role="alert">{apiError(history.error)} <button className="underline" title="Reload the tenant-scoped payment ledger" onClick={() => history.refetch()}>Retry history</button></p>}
      {history.data?.length === 0 && <p>No cash entries recorded.</p>}
      {history.data?.map((entry) => <div key={entry.id} className="mb-2">
        <span>{entry.paid_on} · {money(entry.amount, inv.currency)} · {entry.method}</span>
        {entry.reference && <p>Reference: {entry.reference}</p>}
        {entry.note && <p>{entry.note}</p>}
        {entry.reversal_of_id ? <p>Reversal of payment {entry.reversal_of_id}.</p> : entry.receipt_id ?
          <p>Receipt allocation: correct through <Link className="underline" to="/receipts">Receipts</Link> or <Link className="underline" to="/reconciliation">bank reconciliation</Link>.</p> : Number(entry.amount) > 0 && <>
            {reversed.has(entry.id) ? <span> · reversed</span> : <button className="ml-2 text-rose-600 underline" disabled={!canWrite || !receivable || busy || saved}
              title="Append a full negative offset with a required reason; keep the original entry"
              onClick={() => { setReversing(entry); setExpected(inv.outstanding); setReason(""); setSuccess(null); }}>Reverse payment</button>}
          </>}
      </div>)}
    </div>}
    {reversing && <div className="w-full rounded border border-rose-200 p-2 text-left">
      <p>Reverse {money(reversing.amount, inv.currency)} recorded {reversing.paid_on}? The original entry stays in history.</p>
      <label className="block">Correction reason
        <textarea aria-label="Correction reason" className="input" maxLength={1000} value={reason} disabled={busy} onChange={(e) => setReason(e.target.value)} />
      </label>
      <button className="btn-primary py-1 text-xs" disabled={!canWrite || busy || !reason.trim()} title="Commit an explained full reversal once"
        onClick={() => command.mutate({ path: `/issued/${inv.id}/payments/${reversing.id}/reverse`,
          payload: { currency: inv.currency, expected_outstanding: expected, reason: reason.trim() } })}>{command.isPending ? "Reversing…" : "Confirm reversal"}</button>
      <button className="ml-2" disabled={busy} title="Close reversal confirmation; preserve an unresolved command" onClick={() => setReversing(null)}>Cancel</button>
    </div>}
    {error && <p role="alert" className="text-rose-600">{error}</p>}
    {success && <p role="status" className="text-emerald-600">{success}</p>}
  </div>;
}
