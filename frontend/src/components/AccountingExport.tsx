import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { api, apiError, downloadFile } from "../lib/api";


interface Preview {
  snapshot_id: string; contract: string; entity_name: string; included: number;
  excluded_unapproved: number; excluded_filters: number; excluded_other_currencies: number;
  unassigned_in_period: number; missing_fx: number; token: string | null;
  net: string; tax: string; gross: string; gross_eur: string | null;
  scope: { entity_id: string; date_from: string; date_to: string; currency: string; status: string | null; q: string | null; workflow_state: string | null };
  rows: { invoice_id: string; number: string; supplier: string; net: string; tax: string; gross: string; currency: string }[];
}

export function AccountingExport({ status, q, workflowState }: { status: string; q: string; workflowState: string }) {
  const { user, org } = useAuth();
  const [open, setOpen] = useState(false);
  const [entity, setEntity] = useState("");
  const today = new Date().toISOString().slice(0, 10);
  const [from, setFrom] = useState(`${today.slice(0, 7)}-01`);
  const [to, setTo] = useState(today);
  const [currency, setCurrency] = useState("EUR");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const entities = useQuery<{ id: string; name: string | null; legal_name: string | null }[]>({
    queryKey: ["accounting-export-entities", user?.id, org?.id],
    queryFn: async () => (await api.get("/export/accounting/entities")).data,
    enabled: open && !!user && !!org,
    retry: false,
  });
  const scope = { entity_id: entity, date_from: from, date_to: to, currency, status: status || null, q: q.trim() || null, workflow_state: workflowState || null };
  const identity = JSON.stringify([user?.id, org?.id, scope]);
  const currentIdentity = useRef(identity);
  currentIdentity.current = identity;
  useEffect(() => { setPreview(null); setMessage(null); }, [identity]);
  useEffect(() => { setEntity(""); }, [user?.id, org?.id]);
  const prepare = useMutation({
    mutationFn: async () => ({ data: (await api.post<Preview>("/export/accounting/preview", scope)).data, identity }),
    onSuccess: (result) => {
      if (result.identity !== currentIdentity.current) return;
      setPreview(result.data);
      setMessage(result.data.included === 0 ? "No approved invoices match this scope. Review or assign invoices before exporting." : null);
    },
    onError: (error) => setMessage(`${apiError(error)} Narrow or correct the scope, then preview again.`),
  });
  const download = useMutation({
    mutationFn: async () => {
      if (!preview?.token) throw new Error("Preview eligible invoices first.");
      await downloadFile("/export/accounting/download", "invoices-generic-v2.csv", { method: "post", body: { token: preview.token } });
    },
    onSuccess: () => setMessage("CSV download started. This is generic evidence, not an accepted accountant import."),
    onError: (error) => setMessage(`${apiError(error)} Check your permission and connection, or prepare a fresh preview.`),
  });
  return <div className="min-w-0 max-w-full text-sm">
    <div className="flex min-w-0 flex-wrap items-center gap-1">
      <button className="btn-ghost py-1" onClick={() => setOpen(!open)} title="Choose a legal entity, period and currency, then review the exact approved population before downloading">Accounting CSV</button>
      <button className="btn-ghost py-1" disabled title="Requires a reviewed Xero product/region import template and account/tax mappings">Xero — unconfigured</button>
      <button className="btn-ghost py-1" disabled title="Requires a reviewed QuickBooks product/region import template and account/tax mappings">QuickBooks — unconfigured</button>
    </div>
    {open && <section aria-label="Accounting export preview" className="card mt-2 space-y-3 max-w-3xl">
      <p>Generic CSV v2: original Net/VAT/Gross and currency; recorded conversion is <strong>Gross EUR</strong>. Xero and QuickBooks need a reviewed import contract and mappings before use.</p>
      <div className="grid grid-cols-2 gap-2">
        <div><label htmlFor="accounting-export-entity">Legal entity</label><select id="accounting-export-entity" className="input" value={entity} onChange={(e) => setEntity(e.target.value)}><option value="">Choose an entity</option>{entities.data?.map((item) => <option key={item.id} value={item.id}>{item.name || item.legal_name || "Unnamed entity"}</option>)}</select></div>
        <label>Currency<input className="input" value={currency} maxLength={3} onChange={(e) => setCurrency(e.target.value.toUpperCase())} /></label>
        <label>From<input className="input" type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
        <label>To<input className="input" type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
      </div>
      {entities.isLoading && <p>Loading legal entities…</p>}
      {entities.isError && <p role="alert">Could not load legal entities. Check your access with an administrator, then reopen this preview.</p>}
      <p>List filters: status {status || "any"}; number {q || "any"}; workflow {workflowState || "any"}. Drafts and unapproved invoices are excluded.</p>
      <button className="btn-primary" disabled={prepare.isPending || download.isPending || !entity || !from || !to || currency.length !== 3 || entities.isError} onClick={() => prepare.mutate()} title="Review the selected entity and period; no file or importer action is performed">{prepare.isPending ? "Preparing preview…" : "Preview approved invoices"}</button>
      {preview && <div className="space-y-2">
        <p>{preview.entity_name} · {preview.scope.date_from}–{preview.scope.date_to} · {preview.scope.currency} · {preview.contract}</p>
        <p>Included: {preview.included}. Excluded: {preview.excluded_unapproved} unapproved; {preview.excluded_filters} outside the snapshot/filter selection; {preview.excluded_other_currencies} in other currencies. {preview.unassigned_in_period} invoices in this period have no entity assignment and cannot be included.</p>
        <p>Net {preview.net} / VAT {preview.tax} / Gross {preview.gross} {preview.scope.currency}. Gross EUR: {preview.gross_eur ?? "incomplete"}; {preview.missing_fx} missing conversions.</p>
        <div className="max-h-60 overflow-auto"><table className="w-full text-left"><thead><tr><th>Invoice</th><th>Supplier</th><th>Gross ({preview.scope.currency})</th></tr></thead><tbody>{preview.rows.map((row) => <tr key={row.invoice_id}><td>{row.number}</td><td>{row.supplier}</td><td>{row.gross}</td></tr>)}</tbody></table></div>
        <p>This preview freezes the file for ten minutes. Later source edits will not change these bytes; preview again to include edits. This is not monthly-close approval or importer acceptance.</p>
        <button className="btn-primary" disabled={download.isPending || prepare.isPending || !preview.token} onClick={() => download.mutate()} title="Download exactly the file represented by this preview">{download.isPending ? "Starting download…" : "Download previewed CSV"}</button>
      </div>}
      {message && <p role="status">{message}</p>}
    </section>}
  </div>;
}
