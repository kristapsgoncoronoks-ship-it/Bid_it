import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { api, apiError, downloadFile } from "../lib/api";


interface ExportFormat {
  key: string; action_id: string; label: string; enabled: boolean;
  contract: string | null; reason: string | null; setup_steps: string[];
}

function parseFormats(data: unknown): ExportFormat[] {
  if (!Array.isArray(data) || data.length !== 3) throw new Error("Invalid format catalogue");
  const keys = new Set<string>();
  for (const item of data) {
    if (!item || typeof item !== "object" || !["generic", "xero", "quickbooks"].includes(item.key)
      || keys.has(item.key) || typeof item.action_id !== "string" || typeof item.label !== "string" || !item.label.trim()
      || typeof item.enabled !== "boolean" || !(item.contract === null || typeof item.contract === "string")
      || !(item.reason === null || typeof item.reason === "string")
      || !Array.isArray(item.setup_steps) || !item.setup_steps.every((step: unknown) => typeof step === "string" && step.trim())
      || (!item.enabled && (!item.reason?.trim() || !item.setup_steps.length))
      // No provider execution path exists in this release. A future enablement
      // requires its own reviewed frontend contract, never a new no-op button.
      || (item.key !== "generic" && item.enabled)) throw new Error("Invalid format catalogue");
    keys.add(item.key);
  }
  return data;
}

function BlockedAdapter({ format }: { format: ExportFormat }) {
  const reasonId = useId();
  const provider = format.key === "xero" ? "Xero" : "QuickBooks";
  return <div className="min-w-0">
    <button className="btn-ghost py-1" disabled aria-describedby={reasonId}>{format.label}</button>
    <p id={reasonId} className="text-muted text-xs">{format.reason}</p>
    <details className="text-xs mt-1">
      <summary className="cursor-pointer">{provider} setup requirements</summary>
      <ul className="list-disc pl-5 mt-1">{format.setup_steps.map((step) => <li key={step}>{step}</li>)}</ul>
    </details>
  </div>;
}


interface Preview {
  snapshot_id: string; contract: string; entity_name: string; included: number;
  excluded_unapproved: number; excluded_filters: number; excluded_other_currencies: number;
  unassigned_in_period: number; missing_fx: number; token: string | null;
  net: string; tax: string; gross: string; gross_eur: string | null;
  scope: { entity_id: string; date_from: string; date_to: string; currency: string; status: string | null; q: string | null; workflow_state: string | null };
  rows: { invoice_id: string; number: string; supplier: string; net: string; tax: string; gross: string; currency: string }[];
}

export function AccountingExport({ status, q, workflowState }: { status: string; q: string; workflowState: string }) {
  const { user, org, cacheIdentity } = useAuth();
  const formats = useQuery({
    queryKey: ["accounting-export-formats", cacheIdentity],
    queryFn: async ({ signal }) => parseFormats((await api.get("/export/formats", { signal, timeout: 15_000 })).data),
    enabled: !!user && !!org,
    retry: false,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });
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
  return <div className="text-sm">
    <div>
      <button className="btn-ghost py-1" onClick={() => setOpen(!open)} title="Choose a legal entity, period and currency, then review the exact approved population before downloading">Accounting CSV</button>
    </div>
    <div aria-label="Accounting provider availability" className="mt-2 grid gap-2 sm:grid-cols-2 max-w-3xl">
      {formats.isPending && <p role="status">Checking accounting provider availability…</p>}
      {formats.isError && <div role="alert">
        <p>Provider availability cannot be determined. Xero and QuickBooks exports remain unavailable. Check your access and connection, or ask your administrator.</p>
        <button className="btn-ghost" disabled={formats.isFetching} onClick={() => { void formats.refetch(); }}>{formats.isFetching ? "Checking availability…" : "Retry provider availability"}</button>
      </div>}
      {!formats.isError && formats.data?.filter((format) => format.key !== "generic").map((format) => <BlockedAdapter key={format.key} format={format} />)}
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
