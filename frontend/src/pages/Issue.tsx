import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, apiError, downloadFile, openFile } from "../lib/api";
import { ISSUED_STATUS_LABELS, ISSUED_STATUS_STYLES, money, shortDate } from "../lib/format";
import { useModules } from "../lib/useModules";
import type {
  BulkReminderResult, IssuedInvoice, IssuedLineInput, IssuerProfile,
  Paginated, SendResult, VatScheme,
} from "../lib/types";

const SCHEMES: { value: VatScheme; label: string }[] = [
  { value: "standard", label: "Standard VAT" },
  { value: "reverse_charge", label: "Reverse charge" },
  { value: "intra_eu", label: "Intra-EU supply (exempt)" },
  { value: "exempt", label: "Exempt" },
];

const emptyLine = (): IssuedLineInput => ({ description: "", quantity: "1", unit_price: "0", vat_rate: "21" });

export default function Issue() {
  const qc = useQueryClient();
  const modules = useModules();
  const issuer = useQuery<IssuerProfile>({ queryKey: ["issuer"], queryFn: async () => (await api.get("/issuer")).data });
  const list = useQuery<Paginated<IssuedInvoice>>({
    queryKey: ["issued"],
    queryFn: async () => (await api.get("/issued")).data,
  });

  const enabled = modules.isEnabled("issuing");
  const ready = !!issuer.data?.is_complete;

  if (modules.data && !enabled) {
    return (
      <Gate>
        The invoice issuing module isn't active. Activate it in{" "}
        <Link to="/settings" className="font-medium underline">Settings → Modules</Link>.
      </Gate>
    );
  }
  if (issuer.data && !ready) {
    return (
      <Gate>
        Before issuing,{" "}
        <Link to="/issuer" className="font-medium underline">complete your company registration details</Link>.
      </Gate>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Issue invoices</h1>
          <p className="text-sm text-slate-500">EN 16931-compliant PDF with embedded Factur-X XML.</p>
        </div>
        <div className="flex gap-2">
          <Link to="/issue/reports" className="btn-ghost">Reports</Link>
          <Link to="/issuer" className="btn-ghost">Company details</Link>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <BulkActions />
      </div>

      <NewInvoice
        onCreated={() => qc.invalidateQueries({ queryKey: ["issued"] })}
        defaultPenalty={issuer.data?.default_penalty_rate ?? null}
      />

      <div>
        <h2 className="mb-2 text-sm font-semibold text-slate-600">Issued ({list.data?.total ?? 0})</h2>
        <div className="card overflow-x-auto p-0">
          <table className="w-full text-sm">
            <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-3">Number</th>
                <th className="px-4 py-3">Buyer</th>
                <th className="px-4 py-3">Issued</th>
                <th className="px-4 py-3">Due</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3 text-right">Total</th>
                <th className="px-4 py-3 text-right">Payment</th>
                <th className="px-4 py-3 text-right">Send</th>
                <th className="px-4 py-3 text-right">PDF / XML</th>
              </tr>
            </thead>
            <tbody>
              {list.data?.items.map((inv) => (
                <tr key={inv.id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50">
                  <td className="px-4 py-3 font-medium">{inv.number}</td>
                  <td className="px-4 py-3">
                    {inv.buyer_name}
                    {inv.buyer_email && <div className="text-xs text-slate-400">{inv.buyer_email}</div>}
                  </td>
                  <td className="px-4 py-3 text-slate-500">{shortDate(inv.issue_date)}</td>
                  <td className="px-4 py-3 text-slate-500">{shortDate(inv.due_date)}</td>
                  <td className="px-4 py-3">
                    <span className={`badge ${ISSUED_STATUS_STYLES[inv.status] ?? "bg-slate-100 text-slate-600"}`}>
                      {ISSUED_STATUS_LABELS[inv.status] ?? inv.status}
                    </span>
                    {inv.status === "overdue" && (
                      <div className="mt-0.5 text-xs text-rose-500">
                        {inv.days_overdue}d overdue
                        {Number(inv.penalty_accrued) > 0 && <> · +{money(inv.penalty_accrued, inv.currency)} interest</>}
                      </div>
                    )}
                    {inv.status !== "overdue" && Number(inv.outstanding) > 0 && Number(inv.amount_paid) > 0 && (
                      <div className="mt-0.5 text-xs text-slate-400">{money(inv.outstanding, inv.currency)} left</div>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right font-medium">{money(inv.total, inv.currency)}</td>
                  <td className="px-4 py-3 text-right">
                    <PaymentAction inv={inv} onDone={() => qc.invalidateQueries({ queryKey: ["issued"] })} />
                  </td>
                  <td className="px-4 py-3 text-right">
                    <SendActions inv={inv} onDone={() => qc.invalidateQueries({ queryKey: ["issued"] })} />
                  </td>
                  <td className="px-4 py-3 text-right whitespace-nowrap">
                    <button className="text-brand-600 hover:underline" onClick={() => openFile(`/issued/${inv.id}/pdf?inline=1`)}>
                      View
                    </button>
                    <span className="mx-1.5 text-slate-300">·</span>
                    <button className="text-brand-600 hover:underline" onClick={() => downloadFile(`/issued/${inv.id}/pdf`, `${inv.number}.pdf`)}>
                      PDF
                    </button>
                    <span className="mx-1.5 text-slate-300">·</span>
                    <button className="text-brand-600 hover:underline" onClick={() => downloadFile(`/issued/${inv.id}/xml`, `${inv.number}.xml`)}>
                      XML
                    </button>
                  </td>
                </tr>
              ))}
              {list.data && list.data.items.length === 0 && (
                <tr>
                  <td colSpan={9} className="px-4 py-8 text-center text-slate-400">No invoices issued yet.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// Bulk collections: download every invoice PDF in a period, and send reminders
// to all overdue invoices that have a customer email.
function BulkActions() {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  const zip = useMutation({
    mutationFn: async () => {
      const q = new URLSearchParams();
      if (from) q.set("from", from);
      if (to) q.set("to", to);
      await downloadFile(`/issued/export.zip?${q.toString()}`, `invoices_${from || "all"}_${to || "all"}.zip`);
    },
    onError: (e) => setMsg(apiError(e)),
  });

  const remind = useMutation({
    mutationFn: async () => (await api.post("/issued/reminders/run")).data as BulkReminderResult,
    onSuccess: (r) => setMsg(
      `Sent ${r.sent} reminder${r.sent === 1 ? "" : "s"}` +
      (r.skipped_no_email ? ` · ${r.skipped_no_email} skipped (no email)` : "")
    ),
    onError: (e) => setMsg(apiError(e)),
  });

  return (
    <div className="card flex w-full flex-wrap items-end gap-3">
      <div>
        <label className="label">Period from</label>
        <input type="date" className="input" value={from} onChange={(e) => setFrom(e.target.value)} />
      </div>
      <div>
        <label className="label">to</label>
        <input type="date" className="input" value={to} onChange={(e) => setTo(e.target.value)} />
      </div>
      <button className="btn-ghost" disabled={zip.isPending} onClick={() => { setMsg(null); zip.mutate(); }}>
        {zip.isPending ? "Preparing…" : "Download PDFs (ZIP)"}
      </button>
      <button className="btn-ghost" disabled={remind.isPending} onClick={() => { setMsg(null); remind.mutate(); }}>
        {remind.isPending ? "Sending…" : "Remind all overdue"}
      </button>
      {msg && <span className="text-sm text-slate-500">{msg}</span>}
    </div>
  );
}

// Per-invoice delivery: email the invoice PDF, or send a payment reminder.
function SendActions({ inv, onDone }: { inv: IssuedInvoice; onDone: () => void }) {
  const [note, setNote] = useState<string | null>(null);

  const email = useMutation({
    mutationFn: async () => (await api.post(`/issued/${inv.id}/send`, {})).data as SendResult,
    onSuccess: (r) => { setNote(r.delivered ? "Emailed" : "Queued"); onDone(); },
    onError: (e) => setNote(apiError(e)),
  });
  const remind = useMutation({
    mutationFn: async () => (await api.post(`/issued/${inv.id}/reminder`, {})).data as SendResult,
    onSuccess: (r) => { setNote(r.delivered ? "Reminded" : "Queued"); onDone(); },
    onError: (e) => setNote(apiError(e)),
  });

  const noEmail = !inv.buyer_email;
  const paid = inv.status === "paid";

  return (
    <div className="flex flex-col items-end gap-0.5">
      <div className="flex items-center gap-2">
        <button
          className="text-brand-600 hover:underline disabled:text-slate-300 disabled:no-underline"
          disabled={email.isPending || noEmail}
          title={noEmail ? "Add a customer email first" : "Email the invoice PDF"}
          onClick={() => email.mutate()}
        >
          Email
        </button>
        <span className="text-slate-300">·</span>
        <button
          className="text-amber-600 hover:underline disabled:text-slate-300 disabled:no-underline"
          disabled={remind.isPending || noEmail || paid}
          title={paid ? "Already paid" : noEmail ? "Add a customer email first" : "Send a payment reminder"}
          onClick={() => remind.mutate()}
        >
          Remind
        </button>
      </div>
      {inv.reminder_count > 0 && (
        <span className="text-xs text-slate-400">{inv.reminder_count} reminder{inv.reminder_count === 1 ? "" : "s"} sent</span>
      )}
      {note && <span className="text-xs text-emerald-600">{note}</span>}
    </div>
  );
}

function Gate({ children }: { children: React.ReactNode }) {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold tracking-tight">Issue invoices</h1>
      <div className="card text-sm text-slate-600">{children}</div>
    </div>
  );
}

const emptyBuyer = { buyer_name: "", buyer_email: "", buyer_vat_number: "", buyer_address_line1: "", buyer_postal_code: "", buyer_city: "", buyer_country: "" };

function NewInvoice({ onCreated, defaultPenalty }: { onCreated: () => void; defaultPenalty: string | null }) {
  const [buyer, setBuyer] = useState({ ...emptyBuyer });
  const [scheme, setScheme] = useState<VatScheme>("standard");
  const [penalty, setPenalty] = useState("");
  const [lines, setLines] = useState<IssuedLineInput[]>([emptyLine()]);
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: async () => {
      const payload: Record<string, unknown> = {
        ...buyer,
        buyer_email: buyer.buyer_email || null,
        vat_scheme: scheme,
        lines,
      };
      if (penalty.trim() !== "") payload.penalty_rate = penalty;
      return (await api.post("/issued", payload)).data;
    },
    onSuccess: () => {
      setBuyer({ ...emptyBuyer });
      setLines([emptyLine()]);
      setPenalty("");
      setError(null);
      onCreated();
    },
    onError: (e) => setError(apiError(e)),
  });

  const zero = scheme !== "standard";
  const total = lines.reduce((sum, l) => {
    const net = Number(l.quantity || 0) * Number(l.unit_price || 0);
    const vat = zero ? 0 : (net * Number(l.vat_rate || 0)) / 100;
    return sum + net + vat;
  }, 0);

  const setLine = (i: number, patch: Partial<IssuedLineInput>) =>
    setLines(lines.map((l, idx) => (idx === i ? { ...l, ...patch } : l)));

  return (
    <div className="card space-y-4">
      <h2 className="text-sm font-semibold text-slate-600">New invoice</h2>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Field label="Customer name" v={buyer.buyer_name} on={(v) => setBuyer({ ...buyer, buyer_name: v })} span2 />
        <Field label="Customer VAT no." v={buyer.buyer_vat_number} on={(v) => setBuyer({ ...buyer, buyer_vat_number: v })} />
        <Field label="Customer email" v={buyer.buyer_email} on={(v) => setBuyer({ ...buyer, buyer_email: v })} span2 />
        <Field label="Address" v={buyer.buyer_address_line1} on={(v) => setBuyer({ ...buyer, buyer_address_line1: v })} span2 />
        <Field label="Postcode" v={buyer.buyer_postal_code} on={(v) => setBuyer({ ...buyer, buyer_postal_code: v })} />
        <Field label="City" v={buyer.buyer_city} on={(v) => setBuyer({ ...buyer, buyer_city: v })} />
        <Field label="Country (ISO)" v={buyer.buyer_country} on={(v) => setBuyer({ ...buyer, buyer_country: v })} />
        <div>
          <label className="label">VAT scheme</label>
          <select className="input" value={scheme} onChange={(e) => setScheme(e.target.value as VatScheme)}>
            {SCHEMES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
          </select>
        </div>
        <div>
          <label className="label">Late-payment interest (% p.a.)</label>
          <input
            className="input" inputMode="decimal" value={penalty}
            placeholder={defaultPenalty ? `default ${Number(defaultPenalty)}%` : "none"}
            onChange={(e) => setPenalty(e.target.value)}
          />
        </div>
      </div>

      <div className="overflow-x-auto rounded-lg border border-slate-200">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
            <tr>
              <th className="px-3 py-2">Description</th>
              <th className="px-3 py-2 w-20">Qty</th>
              <th className="px-3 py-2 w-28">Unit price</th>
              <th className="px-3 py-2 w-20">VAT %</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {lines.map((l, i) => (
              <tr key={i} className="border-t border-slate-100">
                <td className="px-3 py-2"><input className="input" value={l.description} onChange={(e) => setLine(i, { description: e.target.value })} /></td>
                <td className="px-3 py-2"><input className="input" value={l.quantity} onChange={(e) => setLine(i, { quantity: e.target.value })} /></td>
                <td className="px-3 py-2"><input className="input" value={l.unit_price} onChange={(e) => setLine(i, { unit_price: e.target.value })} /></td>
                <td className="px-3 py-2"><input className="input" value={l.vat_rate} disabled={zero} onChange={(e) => setLine(i, { vat_rate: e.target.value })} /></td>
                <td className="px-3 py-2 text-right">
                  {lines.length > 1 && (
                    <button className="text-rose-500 hover:underline" onClick={() => setLines(lines.filter((_, idx) => idx !== i))}>remove</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <button className="btn-ghost" onClick={() => setLines([...lines, emptyLine()])}>+ Add line</button>

      {error && <div className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-600">{error}</div>}

      <div className="flex items-center justify-between">
        <span className="text-sm text-slate-500">Total incl. VAT: <span className="font-semibold text-slate-700">{money(total)}</span></span>
        <button
          className="btn-primary"
          disabled={create.isPending || !buyer.buyer_name || lines.some((l) => !l.description)}
          onClick={() => create.mutate()}
        >
          {create.isPending ? "Issuing…" : "Issue invoice"}
        </button>
      </div>
    </div>
  );
}

function Field({ label, v, on, span2 }: { label: string; v: string; on: (v: string) => void; span2?: boolean }) {
  return (
    <div className={span2 ? "sm:col-span-2" : ""}>
      <label className="label">{label}</label>
      <input className="input" value={v} onChange={(e) => on(e.target.value)} />
    </div>
  );
}

// Inline accounts-receivable action: mark fully paid in one click, or open a
// small editor to record a partial / dated payment.
function PaymentAction({ inv, onDone }: { inv: IssuedInvoice; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [amount, setAmount] = useState(inv.amount_paid);
  const [paidDate, setPaidDate] = useState(inv.paid_date ?? "");
  const [error, setError] = useState<string | null>(null);

  const pay = useMutation({
    mutationFn: async (payload: { amount_paid: string; paid_date?: string | null }) =>
      (await api.patch(`/issued/${inv.id}/payment`, payload)).data,
    onSuccess: () => { setOpen(false); setError(null); onDone(); },
    onError: (e) => setError(apiError(e)),
  });

  if (inv.status === "paid") {
    return <span className="text-xs text-emerald-600">{inv.paid_date ? `Paid ${shortDate(inv.paid_date)}` : "Paid"}</span>;
  }

  if (!open) {
    return (
      <div className="flex items-center justify-end gap-2">
        <button
          className="text-emerald-600 hover:underline"
          disabled={pay.isPending}
          onClick={() => pay.mutate({ amount_paid: inv.total, paid_date: new Date().toISOString().slice(0, 10) })}
        >
          Mark paid
        </button>
        <span className="text-slate-300">·</span>
        <button className="text-brand-600 hover:underline" onClick={() => setOpen(true)}>Record…</button>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex items-center gap-1">
        <input
          className="input w-24 py-1 text-right" inputMode="decimal" value={amount}
          onChange={(e) => setAmount(e.target.value)} placeholder="amount"
        />
        <input className="input w-32 py-1" type="date" value={paidDate} onChange={(e) => setPaidDate(e.target.value)} />
        <button
          className="btn-primary py-1 text-xs"
          disabled={pay.isPending}
          onClick={() => pay.mutate({ amount_paid: amount || "0", paid_date: paidDate || null })}
        >
          Save
        </button>
        <button className="text-slate-400 hover:underline" onClick={() => setOpen(false)}>cancel</button>
      </div>
      {error && <span className="text-xs text-rose-500">{error}</span>}
    </div>
  );
}
