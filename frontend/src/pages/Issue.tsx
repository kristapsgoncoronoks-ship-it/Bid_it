import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, apiError, downloadFile } from "../lib/api";
import { ISSUED_STATUS_LABELS, ISSUED_STATUS_STYLES, money, shortDate } from "../lib/format";
import { useModules } from "../lib/useModules";
import type { IssuedInvoice, IssuedLineInput, IssuerProfile, Paginated, VatScheme } from "../lib/types";

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

      <NewInvoice onCreated={() => qc.invalidateQueries({ queryKey: ["issued"] })} />

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
                <th className="px-4 py-3 text-right">Download</th>
              </tr>
            </thead>
            <tbody>
              {list.data?.items.map((inv) => (
                <tr key={inv.id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50">
                  <td className="px-4 py-3 font-medium">{inv.number}</td>
                  <td className="px-4 py-3">{inv.buyer_name}</td>
                  <td className="px-4 py-3 text-slate-500">{shortDate(inv.issue_date)}</td>
                  <td className="px-4 py-3 text-slate-500">{shortDate(inv.due_date)}</td>
                  <td className="px-4 py-3">
                    <span className={`badge ${ISSUED_STATUS_STYLES[inv.status] ?? "bg-slate-100 text-slate-600"}`}>
                      {ISSUED_STATUS_LABELS[inv.status] ?? inv.status}
                    </span>
                    {Number(inv.outstanding) > 0 && Number(inv.amount_paid) > 0 && (
                      <div className="mt-0.5 text-xs text-slate-400">{money(inv.outstanding, inv.currency)} left</div>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right font-medium">{money(inv.total, inv.currency)}</td>
                  <td className="px-4 py-3 text-right">
                    <PaymentAction inv={inv} onDone={() => qc.invalidateQueries({ queryKey: ["issued"] })} />
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button className="text-brand-600 hover:underline" onClick={() => downloadFile(`/issued/${inv.id}/pdf`, `${inv.number}.pdf`)}>
                      PDF
                    </button>
                    <span className="mx-2 text-slate-300">·</span>
                    <button className="text-brand-600 hover:underline" onClick={() => downloadFile(`/issued/${inv.id}/xml`, `${inv.number}.xml`)}>
                      XML
                    </button>
                  </td>
                </tr>
              ))}
              {list.data && list.data.items.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-4 py-8 text-center text-slate-400">No invoices issued yet.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
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

function NewInvoice({ onCreated }: { onCreated: () => void }) {
  const [buyer, setBuyer] = useState({ buyer_name: "", buyer_vat_number: "", buyer_address_line1: "", buyer_postal_code: "", buyer_city: "", buyer_country: "" });
  const [scheme, setScheme] = useState<VatScheme>("standard");
  const [lines, setLines] = useState<IssuedLineInput[]>([emptyLine()]);
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: async () => {
      const payload = { ...buyer, vat_scheme: scheme, lines };
      return (await api.post("/issued", payload)).data;
    },
    onSuccess: () => {
      setBuyer({ buyer_name: "", buyer_vat_number: "", buyer_address_line1: "", buyer_postal_code: "", buyer_city: "", buyer_country: "" });
      setLines([emptyLine()]);
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
