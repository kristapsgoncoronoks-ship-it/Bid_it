import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, apiError, downloadFile, openFile } from "../lib/api";
import { ISSUED_STATUS_LABELS, ISSUED_STATUS_STYLES, money, shortDate } from "../lib/format";
import { useModules } from "../lib/useModules";
import type {
  BulkReminderResult, GenerateResult, IssuedAttachment, IssuedInvoice, IssuedLineInput, IssuerProfile,
  Paginated, Partner, RecurringFrequency, RecurringSchedule, SendResult, VatScheme,
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

      <RecurringSchedules onGenerated={() => qc.invalidateQueries({ queryKey: ["issued"] })} />

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
                  <td className="px-4 py-3 font-medium">
                    {inv.number ?? <span className="italic text-slate-400">Draft — unnumbered</span>}
                    {inv.kind === "penalty" && <span className="badge ml-2 bg-rose-100 text-rose-700">Penalty</span>}
                    {inv.doc_type === "credit_note" && <span className="badge ml-2 bg-violet-100 text-violet-700">Credit note</span>}
                    {inv.doc_type !== "credit_note" && Number(inv.credited_total) > 0 && (
                      <div className="text-xs text-violet-500">−{money(inv.credited_total, inv.currency)} credited</div>
                    )}
                  </td>
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
                    {inv.sent_at && <span className="badge ml-1 bg-sky-50 text-sky-600" title={`Sent ${shortDate(inv.sent_at)}`}>Sent</span>}
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
                    {inv.doc_type === "credit_note" ? (
                      <span className="text-xs text-slate-400">—</span>
                    ) : inv.lifecycle === "draft" || inv.lifecycle === "approved" ? (
                      <DraftActions inv={inv} onDone={() => qc.invalidateQueries({ queryKey: ["issued"] })} />
                    ) : (
                      <div className="flex flex-col items-end gap-1">
                        <PaymentAction inv={inv} onDone={() => qc.invalidateQueries({ queryKey: ["issued"] })} />
                        <CreditAction inv={inv} onDone={() => qc.invalidateQueries({ queryKey: ["issued"] })} />
                        <VoidAction inv={inv} onDone={() => qc.invalidateQueries({ queryKey: ["issued"] })} />
                        {inv.lifecycle !== "written_off" && (
                          <DisputeActions inv={inv} onDone={() => qc.invalidateQueries({ queryKey: ["issued"] })} />
                        )}
                        <DuplicateAction inv={inv} onDone={() => qc.invalidateQueries({ queryKey: ["issued"] })} />
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right">
                    {inv.doc_type === "credit_note" || !inv.number ? (
                      <span className="text-xs text-slate-400">—</span>
                    ) : (
                      <SendActions inv={inv} onDone={() => qc.invalidateQueries({ queryKey: ["issued"] })} />
                    )}
                  </td>
                  <td className="px-4 py-3 text-right whitespace-nowrap">
                    {inv.number ? (
                      <>
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
                      </>
                    ) : (
                      <span className="text-xs text-slate-400">Issue first</span>
                    )}
                    <IssuedAttachments inv={inv} />
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

// Issue a FULL credit note against an invoice (one click + confirm). Partial
// credits are available via the API; the common case here is a full cancellation.
function CreditAction({ inv, onDone }: { inv: IssuedInvoice; onDone: () => void }) {
  const [note, setNote] = useState<string | null>(null);
  const fullyCredited = Number(inv.credited_total) >= Number(inv.total) && Number(inv.total) > 0;

  const credit = useMutation({
    mutationFn: async () => (await api.post(`/issued/${inv.id}/credit-note`, {})).data,
    onSuccess: (cn: IssuedInvoice) => { setNote(`Credited (${cn.number})`); onDone(); },
    onError: (e) => setNote(apiError(e)),
  });

  if (fullyCredited) return <span className="text-xs text-violet-500">Fully credited</span>;

  return (
    <div className="flex items-center gap-1">
      <button
        className="text-xs text-violet-600 hover:underline disabled:text-slate-300 disabled:no-underline"
        disabled={credit.isPending}
        title="Issue a credit note cancelling the remaining amount of this invoice"
        onClick={() => {
          if (window.confirm(`Issue a credit note for the remaining balance of ${inv.number}?`)) credit.mutate();
        }}
      >
        Credit
      </button>
      {note && <span className="text-xs text-slate-500">{note}</span>}
    </div>
  );
}

function VoidAction({ inv, onDone }: { inv: IssuedInvoice; onDone: () => void }) {
  const [err, setErr] = useState<string | null>(null);
  const doVoid = useMutation({
    mutationFn: async (reason: string) => (await api.post(`/issued/${inv.id}/void`, { reason })).data,
    onSuccess: onDone,
    onError: (e) => setErr(apiError(e)),
  });
  // Only an unpaid, un-credited standard invoice can be voided.
  if (
    inv.doc_type === "credit_note" ||
    inv.voided_at !== null ||
    Number(inv.amount_paid) > 0 ||
    Number(inv.credited_total) > 0
  )
    return null;
  return (
    <div className="flex items-center gap-1">
      <button
        className="text-xs text-rose-600 hover:underline disabled:text-slate-300"
        disabled={doVoid.isPending}
        title="Cancel (void) this unpaid invoice"
        onClick={() => {
          const reason = window.prompt(`Void invoice ${inv.number}? Optional reason:`);
          if (reason !== null) doVoid.mutate(reason);
        }}
      >
        Void
      </button>
      {err && <span className="text-xs text-rose-500">{err}</span>}
    </div>
  );
}

// Draft/approved workflow: approve, finalize (issue = allocate the number), or
// cancel. A never-issued draft has no number and is not a receivable.
function DraftActions({ inv, onDone }: { inv: IssuedInvoice; onDone: () => void }) {
  const [err, setErr] = useState<string | null>(null);
  const post = (path: string) => async () => (await api.post(`/issued/${inv.id}/${path}`, {})).data;
  const approve = useMutation({ mutationFn: post("approve"), onSuccess: onDone, onError: (e) => setErr(apiError(e)) });
  const issue = useMutation({ mutationFn: post("issue"), onSuccess: onDone, onError: (e) => setErr(apiError(e)) });
  const cancel = useMutation({ mutationFn: post("cancel"), onSuccess: onDone, onError: (e) => setErr(apiError(e)) });
  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex items-center gap-2">
        {inv.lifecycle === "draft" && (
          <button
            className="text-xs text-indigo-600 hover:underline disabled:text-slate-300"
            disabled={approve.isPending}
            title="Mark this draft as approved (a review gate before issuing)"
            onClick={() => approve.mutate()}
          >
            Approve
          </button>
        )}
        <button
          className="text-xs font-medium text-emerald-600 hover:underline disabled:text-slate-300"
          disabled={issue.isPending}
          title="Finalize: allocate the invoice number and make it a live receivable"
          onClick={() => {
            if (window.confirm("Issue this invoice? A number will be allocated and it becomes final.")) issue.mutate();
          }}
        >
          Issue
        </button>
        <button
          className="text-xs text-rose-600 hover:underline disabled:text-slate-300"
          disabled={cancel.isPending}
          title="Discard this draft (kept for the audit trail as cancelled)"
          onClick={() => {
            if (window.confirm("Cancel this draft?")) cancel.mutate();
          }}
        >
          Cancel
        </button>
      </div>
      {err && <span className="text-xs text-rose-500">{err}</span>}
    </div>
  );
}

// Post-issue receivable workflow: flag/resolve a dispute, or write off bad debt.
function DisputeActions({ inv, onDone }: { inv: IssuedInvoice; onDone: () => void }) {
  const [err, setErr] = useState<string | null>(null);
  const dispute = useMutation({
    mutationFn: async (reason: string) => (await api.post(`/issued/${inv.id}/dispute`, { reason })).data,
    onSuccess: onDone,
    onError: (e) => setErr(apiError(e)),
  });
  const undispute = useMutation({
    mutationFn: async () => (await api.post(`/issued/${inv.id}/undispute`, {})).data,
    onSuccess: onDone,
    onError: (e) => setErr(apiError(e)),
  });
  const writeOff = useMutation({
    mutationFn: async (reason: string) => (await api.post(`/issued/${inv.id}/write-off`, { reason })).data,
    onSuccess: onDone,
    onError: (e) => setErr(apiError(e)),
  });
  return (
    <div className="flex items-center gap-2">
      {inv.lifecycle === "disputed" ? (
        <button
          className="text-xs text-emerald-600 hover:underline disabled:text-slate-300"
          disabled={undispute.isPending}
          title="Resolve the dispute — return to the normal receivable lifecycle"
          onClick={() => undispute.mutate()}
        >
          Resolve
        </button>
      ) : (
        <button
          className="text-xs text-amber-600 hover:underline disabled:text-slate-300"
          disabled={dispute.isPending}
          title="Flag as disputed (the buyer contests it)"
          onClick={() => {
            const r = window.prompt("Reason for the dispute (optional):");
            if (r !== null) dispute.mutate(r);
          }}
        >
          Dispute
        </button>
      )}
      <button
        className="text-xs text-slate-500 hover:underline disabled:text-slate-300"
        disabled={writeOff.isPending}
        title="Write off as bad debt (no longer collectible)"
        onClick={() => {
          const r = window.prompt("Write off this invoice as bad debt? Reason (optional):");
          if (r !== null) writeOff.mutate(r);
        }}
      >
        Write off
      </button>
      {err && <span className="text-xs text-rose-500">{err}</span>}
    </div>
  );
}

// Copy any invoice into a fresh editable draft.
function DuplicateAction({ inv, onDone }: { inv: IssuedInvoice; onDone: () => void }) {
  const [err, setErr] = useState<string | null>(null);
  const dup = useMutation({
    mutationFn: async () => (await api.post(`/issued/${inv.id}/duplicate`, {})).data,
    onSuccess: onDone,
    onError: (e) => setErr(apiError(e)),
  });
  return (
    <div className="flex items-center gap-1">
      <button
        className="text-xs text-slate-500 hover:underline disabled:text-slate-300"
        disabled={dup.isPending}
        title="Copy into a new draft"
        onClick={() => dup.mutate()}
      >
        Duplicate
      </button>
      {err && <span className="text-xs text-rose-500">{err}</span>}
    </div>
  );
}

// Supporting attachments (signed PO, delivery note, contract). Expandable per row.
function IssuedAttachments({ inv }: { inv: IssuedInvoice }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const key = ["issued-attachments", inv.id];
  const list = useQuery<IssuedAttachment[]>({
    queryKey: key,
    queryFn: async () => (await api.get(`/issued/${inv.id}/attachments`)).data,
    enabled: open,
  });
  const upload = useMutation({
    mutationFn: async (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      return (await api.post(`/issued/${inv.id}/attachments`, fd)).data;
    },
    onSuccess: () => { setErr(null); qc.invalidateQueries({ queryKey: key }); },
    onError: (e) => setErr(apiError(e)),
  });
  const remove = useMutation({
    mutationFn: async (aid: string) => (await api.delete(`/issued/${inv.id}/attachments/${aid}`)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: key }),
    onError: (e) => setErr(apiError(e)),
  });
  const count = list.data?.length ?? 0;
  return (
    <div className="mt-1 text-right">
      <button className="text-xs text-slate-500 hover:underline" onClick={() => setOpen((o) => !o)}>
        📎 Files{open && count ? ` (${count})` : ""}
      </button>
      {open && (
        <div className="mt-1 space-y-1 rounded-lg border border-slate-200 p-2 text-left">
          {list.data?.map((a) => (
            <div key={a.id} className="flex items-center justify-between gap-2 text-xs">
              <button
                className="truncate text-brand-600 hover:underline"
                onClick={() => downloadFile(`/issued/${inv.id}/attachments/${a.id}/download`, a.filename)}
              >
                {a.filename}
              </button>
              <button className="text-rose-500 hover:underline" onClick={() => remove.mutate(a.id)}>remove</button>
            </div>
          ))}
          {list.data && list.data.length === 0 && <div className="text-xs text-slate-400">No files yet.</div>}
          <label className="block cursor-pointer text-xs text-brand-600 hover:underline">
            {upload.isPending ? "Uploading…" : "+ Add file"}
            <input
              type="file"
              className="hidden"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) upload.mutate(f); e.target.value = ""; }}
            />
          </label>
          {err && <div className="text-xs text-rose-500">{err}</div>}
        </div>
      )}
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
  const [poRef, setPoRef] = useState("");
  const [exemption, setExemption] = useState("");
  const [partnerId, setPartnerId] = useState("");
  const [lines, setLines] = useState<IssuedLineInput[]>([emptyLine()]);
  const [error, setError] = useState<string | null>(null);

  const partners = useQuery<Partner[]>({
    queryKey: ["partners"],
    queryFn: async () => (await api.get("/partners")).data,
  });
  const selectedPartner = partners.data?.find((p) => p.id === partnerId);

  const pickPartner = (id: string) => {
    setPartnerId(id);
    const p = partners.data?.find((x) => x.id === id);
    if (p) {
      setBuyer({
        ...emptyBuyer,
        buyer_name: p.name, buyer_email: p.email ?? "", buyer_vat_number: p.vat_number ?? "",
        buyer_address_line1: p.address_line1 ?? "", buyer_city: p.city ?? "",
        buyer_postal_code: p.postal_code ?? "", buyer_country: p.country ?? "",
      });
      if (p.penalty_rate) setPenalty(p.penalty_rate);
    }
  };

  const create = useMutation({
    mutationFn: async (asDraft: boolean) => {
      const payload: Record<string, unknown> = {
        ...buyer,
        buyer_email: buyer.buyer_email || null,
        vat_scheme: scheme,
        lines,
        draft: asDraft,
      };
      if (partnerId) payload.partner_id = partnerId;
      if (penalty.trim() !== "") payload.penalty_rate = penalty;
      if (poRef.trim() !== "") payload.po_reference = poRef.trim();
      if (exemption.trim() !== "") payload.tax_exemption_reason = exemption.trim();
      return (await api.post("/issued", payload)).data;
    },
    onSuccess: () => {
      setBuyer({ ...emptyBuyer });
      setLines([emptyLine()]);
      setPenalty("");
      setPoRef("");
      setExemption("");
      setPartnerId("");
      setError(null);
      onCreated();
    },
    onError: (e) => setError(apiError(e)),
  });

  const zero = scheme !== "standard";
  const total = lines.reduce((sum, l) => {
    const gross = Number(l.quantity || 0) * Number(l.unit_price || 0);
    const net = gross * (1 - Number(l.discount_percent || 0) / 100);
    const vat = zero ? 0 : (net * Number(l.vat_rate || 0)) / 100;
    return sum + net + vat;
  }, 0);

  const setLine = (i: number, patch: Partial<IssuedLineInput>) =>
    setLines(lines.map((l, idx) => (idx === i ? { ...l, ...patch } : l)));

  return (
    <div className="card space-y-4">
      <h2 className="text-sm font-semibold text-slate-600">New invoice</h2>

      {(partners.data?.length ?? 0) > 0 && (
        <div>
          <label className="label">Partner (optional)</label>
          <select className="input sm:w-1/2" value={partnerId} onChange={(e) => pickPartner(e.target.value)}>
            <option value="">— One-off customer —</option>
            {partners.data?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          {selectedPartner && (selectedPartner.requires_contract || selectedPartner.requires_acceptance) && (
            <p className="mt-1 text-xs text-amber-600">
              This partner has a pre-invoicing workflow. Issuing is blocked until its documents are signed —{" "}
              <Link to="/partners" className="underline">manage on Partners</Link>.
            </p>
          )}
        </div>
      )}

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
        <Field label="PO reference" v={poRef} on={setPoRef} />
        {zero && (
          <Field
            label="VAT-exemption reason"
            v={exemption}
            on={setExemption}
            span2
          />
        )}
      </div>

      <div className="overflow-x-auto rounded-lg border border-slate-200">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
            <tr>
              <th className="px-3 py-2">Description</th>
              <th className="px-3 py-2 w-20">Qty</th>
              <th className="px-3 py-2 w-28">Unit price</th>
              <th className="px-3 py-2 w-20">Disc %</th>
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
                <td className="px-3 py-2"><input className="input" value={l.discount_percent ?? ""} placeholder="0" onChange={(e) => setLine(i, { discount_percent: e.target.value })} /></td>
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
        <div className="flex items-center gap-2">
          <button
            className="btn-ghost"
            disabled={create.isPending || !buyer.buyer_name || lines.some((l) => !l.description)}
            title="Save as an editable draft (no number until you issue it)"
            onClick={() => create.mutate(true)}
          >
            Save draft
          </button>
          <button
            className="btn-primary"
            disabled={create.isPending || !buyer.buyer_name || lines.some((l) => !l.description)}
            onClick={() => create.mutate(false)}
          >
            {create.isPending ? "Issuing…" : "Issue invoice"}
          </button>
        </div>
      </div>
    </div>
  );
}

const FREQUENCIES: { value: RecurringFrequency; label: string }[] = [
  { value: "weekly", label: "Weekly" },
  { value: "monthly", label: "Monthly" },
  { value: "quarterly", label: "Quarterly" },
  { value: "yearly", label: "Yearly" },
];

// Recurring-invoice schedules: a template + cadence that generates real invoices.
// "Run due now" materialises every invoice that has reached its date.
function RecurringSchedules({ onGenerated }: { onGenerated: () => void }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const list = useQuery<RecurringSchedule[]>({
    queryKey: ["recurring"],
    queryFn: async () => (await api.get("/issued/recurring")).data,
  });

  const run = useMutation({
    mutationFn: async () => (await api.post("/issued/recurring/run")).data as GenerateResult,
    onSuccess: (r) => {
      setMsg(r.generated ? `Generated ${r.generated} invoice${r.generated === 1 ? "" : "s"}: ${r.numbers.join(", ")}` : "Nothing due right now.");
      qc.invalidateQueries({ queryKey: ["recurring"] });
      onGenerated();
    },
    onError: (e) => setMsg(apiError(e)),
  });

  const toggle = useMutation({
    mutationFn: async (v: { id: string; active: boolean }) => (await api.patch(`/issued/recurring/${v.id}`, { active: v.active })).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["recurring"] }),
  });
  const remove = useMutation({
    mutationFn: async (id: string) => api.delete(`/issued/recurring/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["recurring"] }),
  });

  return (
    <div className="card space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-slate-600">Recurring schedules</h2>
          <p className="text-xs text-slate-400">Generate invoices automatically on a cadence (retainers, subscriptions, rent).</p>
        </div>
        <div className="flex items-center gap-2">
          <button className="btn-ghost" disabled={run.isPending} onClick={() => { setMsg(null); run.mutate(); }}>
            {run.isPending ? "Running…" : "Run due now"}
          </button>
          <button className="btn-primary" onClick={() => setOpen((o) => !o)}>{open ? "Close" : "New schedule"}</button>
        </div>
      </div>

      {msg && <div className="rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-600">{msg}</div>}

      {open && <NewRecurring onCreated={() => { setOpen(false); qc.invalidateQueries({ queryKey: ["recurring"] }); }} />}

      {(list.data?.length ?? 0) > 0 && (
        <div className="overflow-x-auto rounded-lg border border-slate-200">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
              <tr>
                <th className="px-3 py-2">Schedule</th>
                <th className="px-3 py-2">Cadence</th>
                <th className="px-3 py-2">Next run</th>
                <th className="px-3 py-2">Generated</th>
                <th className="px-3 py-2">State</th>
                <th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {list.data!.map((s) => (
                <tr key={s.id} className="border-t border-slate-100">
                  <td className="px-3 py-2 font-medium">{s.title || "Untitled schedule"}</td>
                  <td className="px-3 py-2 text-slate-500">
                    Every {s.interval > 1 ? `${s.interval} ` : ""}{s.frequency.replace(/ly$/, s.interval > 1 ? "s" : "")}
                  </td>
                  <td className="px-3 py-2 text-slate-500">{shortDate(s.next_run_date)}</td>
                  <td className="px-3 py-2 text-slate-500">{s.generated_count}</td>
                  <td className="px-3 py-2">
                    <span className={`badge ${s.active ? "bg-emerald-100 text-emerald-700" : "bg-slate-100 text-slate-500"}`}>
                      {s.active ? "active" : "paused"}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right whitespace-nowrap">
                    <button className="text-brand-600 hover:underline" onClick={() => toggle.mutate({ id: s.id, active: !s.active })}>
                      {s.active ? "pause" : "resume"}
                    </button>
                    <span className="mx-1.5 text-slate-300">·</span>
                    <button
                      className="text-rose-500 hover:underline"
                      onClick={() => { if (window.confirm("Delete this schedule? Invoices already generated are kept.")) remove.mutate(s.id); }}
                    >
                      delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function NewRecurring({ onCreated }: { onCreated: () => void }) {
  const [title, setTitle] = useState("");
  const [buyerName, setBuyerName] = useState("");
  const [buyerEmail, setBuyerEmail] = useState("");
  const [scheme, setScheme] = useState<VatScheme>("standard");
  const [frequency, setFrequency] = useState<RecurringFrequency>("monthly");
  const [interval, setInterval] = useState("1");
  const [startDate, setStartDate] = useState(new Date().toISOString().slice(0, 10));
  const [endDate, setEndDate] = useState("");
  const [lines, setLines] = useState<IssuedLineInput[]>([emptyLine()]);
  const [error, setError] = useState<string | null>(null);

  const zero = scheme !== "standard";
  const setLine = (i: number, patch: Partial<IssuedLineInput>) =>
    setLines(lines.map((l, idx) => (idx === i ? { ...l, ...patch } : l)));

  const create = useMutation({
    mutationFn: async () => {
      const payload = {
        template: { buyer_name: buyerName, buyer_email: buyerEmail || null, vat_scheme: scheme, lines },
        frequency,
        interval: Number(interval) || 1,
        start_date: startDate,
        end_date: endDate || null,
        title: title || null,
      };
      return (await api.post("/issued/recurring", payload)).data;
    },
    onSuccess: onCreated,
    onError: (e) => setError(apiError(e)),
  });

  return (
    <div className="space-y-3 rounded-lg border border-slate-200 bg-slate-50/50 p-3">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Field label="Schedule title" v={title} on={setTitle} span2 />
        <div>
          <label className="label">Frequency</label>
          <select className="input" value={frequency} onChange={(e) => setFrequency(e.target.value as RecurringFrequency)}>
            {FREQUENCIES.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
          </select>
        </div>
        <Field label="Customer name" v={buyerName} on={setBuyerName} span2 />
        <Field label="Customer email" v={buyerEmail} on={setBuyerEmail} />
        <div>
          <label className="label">Every N periods</label>
          <input className="input" inputMode="numeric" value={interval} onChange={(e) => setInterval(e.target.value)} />
        </div>
        <div>
          <label className="label">Start date</label>
          <input className="input" type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
        </div>
        <div>
          <label className="label">End date (optional)</label>
          <input className="input" type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
        </div>
        <div>
          <label className="label">VAT scheme</label>
          <select className="input" value={scheme} onChange={(e) => setScheme(e.target.value as VatScheme)}>
            {SCHEMES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
          </select>
        </div>
      </div>

      <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
            <tr>
              <th className="px-3 py-2">Description</th>
              <th className="px-3 py-2 w-20">Qty</th>
              <th className="px-3 py-2 w-28">Unit price</th>
              <th className="px-3 py-2 w-20">Disc %</th>
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
                <td className="px-3 py-2"><input className="input" value={l.discount_percent ?? ""} placeholder="0" onChange={(e) => setLine(i, { discount_percent: e.target.value })} /></td>
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

      <div className="flex justify-end">
        <button
          className="btn-primary"
          disabled={create.isPending || !buyerName || lines.some((l) => !l.description)}
          onClick={() => create.mutate()}
        >
          {create.isPending ? "Creating…" : "Create schedule"}
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
