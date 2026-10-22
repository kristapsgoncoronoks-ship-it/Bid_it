import { useMutation, useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { KpiCard } from "../components/KpiCard";
import { ModuleInactive } from "../components/ModuleGate";
import { useAuth } from "../auth/AuthContext";
import { EmptyState, PageHeader, QueryState, Skeleton } from "../components/ui";
import { api, apiError } from "../lib/api";
import { EXPENSE_STATUS_STYLES, decimalMoney, money, shortDate } from "../lib/format";
import { useModules } from "../lib/useModules";
import {
  EXPENSE_CATEGORIES,
  type ExpenseItemInput,
  type ExpenseReport,
  type ExpenseSummary,
  type ExpenseTransaction,
  type Paginated,
} from "../lib/types";
import { useConfirm } from "../components/ui/useConfirm";
import { checkSavedInvoiceCommand, hasSavedInvoiceCommand, invoiceCommand, retrySavedExpenseReport } from "../lib/invoiceCommands";

import { captureManualDraft, MANUAL_EXPENSE_SLOT, manualDraftAmounts, manualDraftResponse, manualExpenseLookup, readManualReference, type ExpenseCommandScope, type ManualDraftPayload, type SavedManualDraft } from "../lib/manualExpenseCommand";

const emptyItem = (): ExpenseItemInput => ({
  spend_date: new Date().toISOString().slice(0, 10),
  category: "travel",
  description: "",
  amount: "0",
  vat_amount: "0",
  payment_method: "personal",
  comment: "",
});

export default function Expenses() {
  const { hasPerm, cacheIdentity } = useAuth();
  const isManager = hasPerm("expense.approve");
  const modules = useModules();
  const enabled = modules.isEnabled("expenses");

  const summary = useQuery<ExpenseSummary>({ queryKey: ["expenses", "summary"], queryFn: async () => (await api.get("/expenses/summary")).data, enabled });
  const mine = useQuery<Paginated<ExpenseReport>>({ queryKey: ["expenses", "mine"], queryFn: async () => (await api.get("/expenses?mine=true")).data, enabled });
  const pending = useQuery<Paginated<ExpenseReport>>({
    queryKey: ["expenses", "pending"],
    queryFn: async () => (await api.get("/expenses?status=submitted")).data,
    enabled: enabled && isManager,
  });

  if (modules.data && !enabled) {
    return (
      <div className="space-y-4">
        <PageHeader title="Expenses" />
        <ModuleInactive name="employee expenses" />
      </div>
    );
  }

  const s = summary.data;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Expenses"
        actions={
          isManager && (
            <Link to="/reimbursements" className="text-sm text-brand-600 hover:underline">
              Reimbursements →
            </Link>
          )
        }
      />

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <KpiCard label="Awaiting reimbursement" value={s ? money(s.my_reimbursable) : "—"} accent="emerald" sub="approved · mine" />
        <KpiCard label="Reclaimable VAT" value={s ? money(s.reclaimable_vat) : "—"} accent="amber" sub="on my expenses" />
        <KpiCard label="Submitted" value={s ? String(s.my_submitted) : "—"} sub="mine, awaiting review" />
        {isManager && <KpiCard label="To approve" value={s ? String(s.pending_approvals) : "—"} accent="rose" sub="team, awaiting me" />}
      </div>

      <BankDetailsCard />

      <AvailableExpenses key={cacheIdentity} enabled={!!enabled} />

      <NewReport key={"manual:" + cacheIdentity} enabled={!!enabled} />

      {isManager && (
        <ReportTable
          title={`Awaiting my approval${pending.data ? ` (${pending.data.total})` : ""}`}
          query={pending}
          showEmployee
          emptyTitle="Nothing awaiting your approval"
        />
      )}

      <ReportTable
        title={`My reports${mine.data ? ` (${mine.data.total})` : ""}`}
        query={mine}
        emptyTitle="No expense reports yet"
      />
    </div>
  );
}

type ImportOutcome = {
  imported: number; method: string; batch_id: string; replayed: boolean;
  warnings: string[]; available: number; assigned: number; binned: number; unavailable: number;
};

function selectedAmount(rows: ExpenseTransaction[]): string {
  const cents = rows.reduce((sum, row) => {
    if (!/^\d+(?:\.\d{1,2})?$/.test(row.amount)) throw new Error("Invalid amount in the inbox.");
    const [whole, fraction = ""] = row.amount.split(".");
    return sum + BigInt(whole) * 100n + BigInt(fraction.padEnd(2, "0"));
  }, 0n);
  return String(cents / 100n) + "." + String(cents % 100n).padStart(2, "0");
}

function AvailableExpenses({ enabled }: { enabled: boolean }) {
  const { user, org, hasPerm } = useAuth();
  const { confirm, dialog } = useConfirm();
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [title, setTitle] = useState("");
  const [account, setAccount] = useState("");
  const [currency, setCurrency] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [importWarnings, setImportWarnings] = useState<string[]>([]);
  const scope = { userId: user?.id ?? "", orgId: org?.id ?? "" };
  const canWrite = enabled && !!user && !!org && hasPerm("expense.write");
  const queryKey = ["expenses", "transactions", scope.orgId, scope.userId];
  const importSlot = "/expenses/import/bank-statement";
  const reportSlot = "/expenses/from-selected";
  const removeSlot = "/expenses/remove";
  const lookup = (key: string) => "/expenses/commands/" + key + "/result";
  const txns = useQuery<ExpenseTransaction[]>({
    queryKey,
    queryFn: async () => (await api.get("/expenses/transactions")).data,
    enabled,
  });
  async function refreshInbox() {
    try {
      await qc.invalidateQueries({ queryKey, exact: true }, { throwOnError: true });
    } catch {
      setError("The change was saved, but the inbox could not refresh. Retry loading the inbox; do not repeat the write.");
    }
  }
  async function reportSaved(report: ExpenseReport) {
    setSelected(new Set()); setTitle("");
    setMsg("Report saved: " + report.title + " (" + report.currency + " " + report.total + ").");
    setError(null);
    await refreshInbox();
    try {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["expenses", "mine"] }, { throwOnError: true }),
        qc.invalidateQueries({ queryKey: ["expenses", "summary"] }, { throwOnError: true }),
      ]);
    } catch {
      setError("The report was saved, but a report list could not refresh. Retry loading the list.");
    }
  }
  async function importSaved(result: ImportOutcome) {
    setMsg(result.replayed
      ? "This statement was already imported; no candidates were recreated."
      : "Import committed: " + result.imported + " new expense candidate(s).");
    setImportWarnings(result.warnings ?? []);
    setError(null);
    await refreshInbox();
  }
  const importStmt = useMutation({
    retry: false,
    mutationFn: async (file: File) => {
      if (!canWrite) throw new Error("Expense write permission and an active company are required.");
      const data = new FormData();
      data.append("file", file);
      data.append("account_reference", account.trim());
      if (currency.trim()) data.append("statement_currency", currency.trim().toUpperCase());
      const hash = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
      const sha256 = Array.from(new Uint8Array(hash), (v) => v.toString(16).padStart(2, "0")).join("");
      return invoiceCommand<ImportOutcome>(importSlot, data, scope, lookup, undefined,
        importSlot, false, { sha256, account: account.trim(), currency: currency.trim().toUpperCase() });
    },
    onSuccess: importSaved,
    onError: (e) => setError(apiError(e)),
  });
  const del = useMutation({
    retry: false,
    mutationFn: async (row: ExpenseTransaction) => {
      if (!canWrite) throw new Error("Expense write permission is required.");
      return invoiceCommand(
        "/expenses/transactions/" + row.id + "?version=" + row.version, null,
        scope, lookup, undefined, removeSlot, false, { id: row.id, version: row.version }, "delete");
    },
    onSuccess: async (_result, row) => {
      setSelected((current) => { const next = new Set(current); next.delete(row.id); return next; });
      setMsg("Removal recorded. The current inbox is refreshed below. Restore eligible rows from Trash; ask an administrator if you do not have restore access.");
      setError(null);
      await refreshInbox();
    },
    onError: (e) => setError(apiError(e)),
  });
  const rows = txns.data ?? [];
  const picked = rows.filter((row) => selected.has(row.id));
  const currencies = new Set(picked.map((row) => row.currency));
  const mixed = currencies.size > 1;
  const legacy = picked.some((row) => row.source === "bank_statement" && !row.import_batch_id && !row.currency_source);
  const selectedCurrency = picked[0]?.currency ?? "";
  const total = picked.length && !mixed ? selectedAmount(picked) : "";
  const createFrom = useMutation({
    retry: false,
    mutationFn: async () => {
      if (!canWrite || !picked.length || mixed || legacy) throw new Error("Select verified transactions in one currency first.");
      return invoiceCommand<ExpenseReport>("/expenses", {
        title: title.trim() || "New report", currency: selectedCurrency,
        transaction_ids: picked.map((row) => row.id),
        transaction_versions: Object.fromEntries(picked.map((row) => [row.id, row.version])),
      }, scope, lookup, undefined, reportSlot, true);
    },
    onSuccess: reportSaved,
    onError: (e) => setError(apiError(e)),
  });
  const recover = useMutation({
    retry: false,
    mutationFn: async ({ slot, retry }: { slot: string; retry?: boolean }) => {
      if (!canWrite) throw new Error("Refresh your expense permissions before checking this request.");
      if (retry) return { slot, result: await retrySavedExpenseReport<ExpenseReport>(scope) };
      const result = await checkSavedInvoiceCommand<ImportOutcome | ExpenseReport | { id: string }>(slot, scope, lookup);
      return { slot, result };
    },
    onSuccess: async ({ slot, result }) => {
      if (!result) {
        setError("No committed outcome is visible yet. Check again when connected. A retry must use the original inputs and saved request key.");
        return;
      }
      if (slot === importSlot) await importSaved(result as ImportOutcome);
      else if (slot === reportSlot) await reportSaved(result as ExpenseReport);
      else {
        setMsg("The previous removal committed. The inbox below shows its current state.");
        setError(null); await refreshInbox();
      }
    },
    onError: (e) => setError(apiError(e)),
  });
  const busy = importStmt.isPending || del.isPending || createFrom.isPending || recover.isPending;
  const toggle = (id: string) => setSelected((s) => { const next = new Set(s); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  const pending = [
    [importSlot, "import"], [reportSlot, "report"], [removeSlot, "removal"],
  ].filter(([slot]) => hasSavedInvoiceCommand(slot, scope));
  return (
    <div className="card space-y-3">
      {dialog}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-600">Available expenses{rows.length ? " (" + rows.length + ")" : ""}</h2>
          <p className="text-xs text-slate-500">Import statement debits, then select one currency for a draft report. This does not record a payment.</p>
        </div>
        <button className="btn-ghost" disabled={!canWrite || busy || !account.trim()} onClick={() => fileRef.current?.click()}>
          {importStmt.isPending ? "Reading…" : "Import bank statement"}
        </button>
      </div>
      <div className="flex flex-wrap gap-3">
        <label className="text-sm">Statement account reference
          <input className="input" value={account} maxLength={120} disabled={busy} onChange={(e) => setAccount(e.target.value)} placeholder="e.g. operating card" />
        </label>
        <label className="text-sm">Currency if missing from the statement
          <input className="input" value={currency} maxLength={3} disabled={busy} onChange={(e) => setCurrency(e.target.value.toUpperCase())} placeholder="e.g. EUR" />
        </label>
      </div>
      <p className="text-xs text-slate-500">Use the same account reference for reimports. PDF and CSV are supported; convert other formats first. Enter an account reference to enable import.</p>
      {!canWrite && <p role="status">Expense write permission and an enabled module are required.</p>}
      <input aria-label="Statement file" ref={fileRef} type="file" accept=".pdf,.csv" className="hidden" disabled={!canWrite || busy || !account.trim()}
        onChange={(e) => { const f = e.target.files?.[0]; if (f) importStmt.mutate(f); e.target.value = ""; }} />
      {busy && <p role="status">Completing the expense request and refreshing its saved outcome…</p>}
      {msg && <div role="status" className="rounded-lg bg-sky-50 px-3 py-2 text-sm text-sky-700">{msg}</div>}
      {error && <div role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-600">{error}</div>}
      {importWarnings.length > 0 && <ul className="list-disc pl-5 text-sm">{importWarnings.map((warning, i) => <li key={i}>{warning}</li>)}</ul>}
      {pending.map(([slot, label]) => <div key={slot} className="flex flex-wrap gap-2 text-sm">
        <span>A previous {label} request needs its outcome checked.</span>
        <button className="btn-ghost" disabled={!canWrite || busy} onClick={() => recover.mutate({ slot })}>Check previous {label} outcome</button>
        {slot === reportSlot && <button className="btn-ghost" disabled={!canWrite || busy} onClick={() => recover.mutate({ slot, retry: true })}>Retry original report request</button>}
      </div>)}
      {!enabled ? <p role="status">Checking expense availability…</p> : txns.isPending ? <p role="status">Loading available transactions…</p> : txns.isError ? (
        <div role="alert"><p>The available transactions could not be loaded. The inbox is not confirmed empty.</p><button className="btn-ghost" disabled={txns.isFetching} onClick={() => { void txns.refetch(); }}>Retry loading inbox</button></div>
      ) : rows.length === 0 ? (
        <p className="py-4 text-center text-sm text-text-secondary">No available transactions. Import a statement to get started.</p>
      ) : (
        <>
          <div className="overflow-x-auto rounded-lg border border-slate-200">
            <table className="w-full text-sm"><thead className="bg-slate-50 text-left text-xs uppercase text-slate-500"><tr><th className="px-3 py-2">Select</th><th className="px-3 py-2">Date</th><th className="px-3 py-2">Description</th><th className="px-3 py-2 text-right">Amount</th><th className="px-3 py-2 text-right">Action</th></tr></thead>
              <tbody>{rows.map((row) => <tr key={row.id} className="border-t border-slate-100">
                <td className="px-3 py-2"><input aria-label={"Select " + row.description} type="checkbox" disabled={busy || !canWrite} checked={selected.has(row.id)} onChange={() => toggle(row.id)} /></td>
                <td className="px-3 py-2 text-slate-500">{shortDate(row.txn_date)}</td><td className="px-3 py-2">{row.description}</td><td className="px-3 py-2 text-right font-medium">{money(row.amount, row.currency)}</td>
                <td className="px-3 py-2 text-right"><button className="text-rose-500 hover:underline" disabled={!canWrite || busy} onClick={async () => {
                  if (await confirm({ title: "Remove this transaction?", body: "Move this available candidate to Trash. Eligible rows can be restored during the existing retention period; no bank payment is deleted.", confirmLabel: "Remove" })) del.mutate(row);
                }}>remove</button></td>
              </tr>)}</tbody>
            </table>
          </div>
          {selected.size > 0 && <div className="space-y-2">
            <label className="text-sm" htmlFor="selected-report-title">Report title</label>
            <input id="selected-report-title" className="input" value={title} disabled={busy} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. October card expenses" />
            {(mixed || legacy) && <p role="alert">{mixed ? "Mixed currencies cannot be added together. Select one currency for each report." : "Legacy statement currency is unverified. Review its original source before reporting."}</p>}
            <button className="btn-primary" disabled={!canWrite || busy || mixed || legacy || !picked.length} onClick={() => createFrom.mutate()}>
              {createFrom.isPending ? "Creating…" : "Create report from " + picked.length + (total ? " · " + money(total, selectedCurrency) : "")}
            </button>
          </div>}
        </>
      )}
    </div>
  );
}

function ReportTable({
  title,
  query,
  showEmployee,
  emptyTitle,
}: {
  title: string;
  query: UseQueryResult<Paginated<ExpenseReport>>;
  showEmployee?: boolean;
  emptyTitle: string;
}) {
  return (
    <div>
      <h2 className="mb-2 text-sm font-semibold text-slate-600">{title}</h2>
      <div className="card overflow-x-auto p-0">
        <QueryState
          query={query}
          loading={<Skeleton className="m-4 h-32 w-[calc(100%-2rem)]" />}
          isEmpty={(d) => d.items.length === 0}
          empty={<EmptyState title={emptyTitle} />}
          errorTitle="Couldn’t load expense reports"
        >
          {(d) => (
            <table className="w-full text-sm">
              <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-3">Report</th>
                  {showEmployee && <th className="px-4 py-3">Employee</th>}
                  <th className="px-4 py-3">Submitted</th>
                  <th className="px-4 py-3 text-right">VAT</th>
                  <th className="px-4 py-3 text-right">Total</th>
                  <th className="px-4 py-3">Status</th>
                </tr>
              </thead>
              <tbody>
                {d.items.map((r) => (
                  <tr key={r.id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50">
                    <td className="px-4 py-3">
                      <Link to={`/expenses/${r.id}`} className="font-medium text-brand-600 hover:underline">{r.title}</Link>
                    </td>
                    {showEmployee && <td className="px-4 py-3 text-slate-500">{r.employee_name}</td>}
                    <td className="px-4 py-3 text-slate-500">{r.submitted_at ? shortDate(r.submitted_at) : "—"}</td>
                    <td className="px-4 py-3 text-right text-slate-500">{money(r.vat_total, r.currency)}</td>
                    <td className="px-4 py-3 text-right font-medium">{money(r.total, r.currency)}</td>
                    <td className="px-4 py-3"><span className={`badge ${EXPENSE_STATUS_STYLES[r.status] ?? ""}`}>{r.status}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </QueryState>
      </div>
    </div>
  );
}

type ManualSaveIntention = {
  key: string; scope: ExpenseCommandScope; payload: ManualDraftPayload; signal: AbortSignal;
};

function NewReport({ enabled }: { enabled: boolean }) {
  const qc = useQueryClient();
  const { user, org, hasPerm } = useAuth();
  const scope = { userId: user?.id ?? "", orgId: org?.id ?? "" };
  const canWrite = enabled && !!user && !!org && hasPerm("expense.write");
  const [title, setTitle] = useState("");
  const [currency, setCurrency] = useState("EUR");
  const [items, setItems] = useState<ExpenseItemInput[]>([emptyItem()]);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const originalSave = useRef<ManualSaveIntention | null>(null);
  const saveController = useRef<AbortController | null>(null);
  const checkController = useRef<AbortController | null>(null);
  const active = useRef(true);
  const [saved, setSaved] = useState<{ report: SavedManualDraft; key: string } | null>(null);
  const [readState, setReadState] = useState<"loading" | "failed" | "ready" | null>(null);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      // A browser cancellation does not undo server work. The shared helper
      // retains the saved reference if this request was already dispatched.
      saveController.current?.abort();
      checkController.current?.abort();
    };
  }, []);

  let reference: { key: string; digest: string } | null = null;
  let referenceError: string | null = null;
  try { reference = readManualReference(scope); }
  catch (e) { referenceError = apiError(e); }
  const unresolved = !!reference || !!referenceError;
  const frozen = busy || unresolved;
  let total: string | null = null;
  let amountError: string | null = null;
  try { total = manualDraftAmounts(items).total; }
  catch (e) { amountError = apiError(e); }

  async function readSaved(report: SavedManualDraft) {
    if (!active.current) return;
    setReadState("loading");
    // Read the current record independently of the immutable command receipt.
    // Refresh only the report list/summary used on this page, and await them.
    const results = await Promise.allSettled([
      qc.fetchQuery({ queryKey: ["expense", report.id], staleTime: 0, retry: false,
        queryFn: async () => {
          const current = (await api.get(`/expenses/${report.id}`)).data;
          if (manualDraftResponse(current, "", "write").id !== report.id) throw new Error("The current report response did not match the saved ID.");
          return current;
        } }),
      qc.invalidateQueries({ queryKey: ["expenses", "mine"], exact: true }, { throwOnError: true }),
      qc.invalidateQueries({ queryKey: ["expenses", "summary"], exact: true }, { throwOnError: true }),
    ]);
    if (!active.current) return;
    if (results.some((result) => result.status === "rejected")) {
      setReadState("failed");
      setError("The manual draft was saved, but its current report or totals could not load. Retry loading the saved report; do not save a replacement.");
    } else {
      setReadState("ready");
      setError(null);
      setOpen(false);
    }
  }

  const create = useMutation({
    retry: false,
    mutationFn: async (request: ManualSaveIntention) => {
      return invoiceCommand<SavedManualDraft>("/expenses", request.payload, request.scope,
        manualExpenseLookup, request.key, MANUAL_EXPENSE_SLOT, false, undefined, "post", manualDraftResponse, request.signal);
    },
    onSuccess: async (report, request) => {
      if (!active.current) return;
      setSaved({ report, key: request.key });
      setError(null);
      await readSaved(report);
    },
    onError: (e) => { if (active.current) setError(apiError(e)); },
    onSettled: () => {
      busyRef.current = false;
      if (active.current) setBusy(false);
    },
  });

  const check = useMutation({
    retry: false,
    mutationFn: async ({ scope: requestScope, key, signal }: { scope: ExpenseCommandScope; key: string; signal: AbortSignal }) => {
      // Keep known receipts checkable without a write, even after metadata clears.
      const pending = readManualReference(requestScope);
      if (pending) {
        return checkSavedInvoiceCommand<SavedManualDraft>(MANUAL_EXPENSE_SLOT, requestScope,
          manualExpenseLookup, manualDraftResponse, signal);
      }
      const result = await api.get(manualExpenseLookup(key), { signal, headers: {
        "X-Expected-Org-ID": requestScope.orgId, "X-Expected-User-ID": requestScope.userId,
      } });
      return manualDraftResponse(result.data, key, "lookup");
    },
    onSuccess: async (report, request) => {
      if (!active.current) return;
      if (!report) {
        setError("No committed manual save is visible yet. Its outcome remains unresolved. Check again when connected; do not create a replacement draft.");
        return;
      }
      setSaved({ report, key: request.key });
      setError(null);
      await readSaved(report);
    },
    onError: (e) => { if (active.current) setError("Could not check the manual save. " + apiError(e) + " Keep its reference and check again when access or connection is restored."); },
    onSettled: () => {
      busyRef.current = false;
      if (active.current) setBusy(false);
    },
  });

  function saveDraft() {
    if (busyRef.current || !canWrite || saved) return;
    try {
      // Do not replace an unresolved reference, including after refresh has lost
      // the original inputs. Status checking below never posts.
      if (readManualReference(scope)) throw new Error("A previous manual save has an unresolved outcome. Check it before creating another draft.");
      const payload = captureManualDraft(title, currency, items);
      const controller = new AbortController();
      const request = { key: crypto.randomUUID(), scope: Object.freeze({ ...scope }), payload, signal: controller.signal };
      saveController.current = controller;
      originalSave.current = request;
      busyRef.current = true;
      setBusy(true);
      setError(null);
      create.mutate(request);
    } catch (e) { setError(apiError(e)); }
  }

  function checkDraft() {
    if (busyRef.current || !canWrite) return;
    try {
      const pending = readManualReference(scope);
      const key = pending?.key ?? saved?.key;
      if (!key) throw new Error("No saved manual reference remains. Check My reports before starting another draft.");
      busyRef.current = true;
      setBusy(true);
      setError(null);
      const controller = new AbortController();
      checkController.current = controller;
      check.mutate({ scope: { ...scope }, key, signal: controller.signal });
    } catch (e) { setError(apiError(e)); }
  }

  function retryOriginalSave() {
    if (busyRef.current || !canWrite || saved) return;
    try {
      const pending = readManualReference(scope);
      const request = originalSave.current;
      if (!pending || !request || pending.key !== request.key || request.scope.userId !== scope.userId || request.scope.orgId !== scope.orgId) {
        throw new Error("The original manual inputs are unavailable. Check the saved outcome; do not create a replacement draft.");
      }
      busyRef.current = true; setBusy(true); setError(null);
      // Only this still-mounted form has the original frozen inputs. The shared
      // helper checks the receipt first and, if absent, replays the same key.
      create.mutate(request);
    } catch (e) { setError(apiError(e)); }
  }

  const setItem = (i: number, patch: Partial<ExpenseItemInput>) => {
    if (!busyRef.current && !unresolved) setItems((current) => current.map((it, idx) => idx === i ? { ...it, ...patch } : it));
  };
  const beginDraft = () => {
    if (busyRef.current || unresolved || !canWrite || (saved && readState !== "ready")) return;
    originalSave.current = null;
    setSaved(null); setReadState(null); setTitle(""); setCurrency("EUR"); setItems([emptyItem()]); setError(null); setOpen(true);
  };

  return (
    <div className="space-y-3">
      {!canWrite && <p role="status">Expense write permission and an enabled module are required to save or check a manual draft.</p>}
      {busy && <p role="status">{check.isPending ? "Checking the manual save and loading its recorded outcome…" : "Saving the manual draft and loading its recorded outcome…"}</p>}
      {saved && <div role="status" className="rounded-lg bg-sky-50 px-3 py-2 text-sm text-sky-700">
        <p>Manual draft saved: {saved.report.title} · {saved.report.currency} {saved.report.total} · ID {saved.report.id}.</p>
        <Link to={`/expenses/${saved.report.id}`} className="font-medium underline">Open saved draft</Link>
        {readState === "loading" && <p>Loading the current report and totals…</p>}
        {readState === "ready" && <p>The current report and totals have loaded. Saving only created a draft; submission and payment are separate actions.</p>}
      </div>}
      {(error || referenceError || (open && amountError)) && <div role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-600">{error || referenceError || amountError}</div>}
      {(reference || saved) && <div className="flex flex-wrap items-center gap-2 text-sm">
        {reference && <span>A previous manual save needs its outcome checked. Closing the editor does not cancel it. Save reference: {reference.key}.</span>}
        <button className="btn-ghost" disabled={!canWrite || busy} onClick={checkDraft}>Check previous manual draft outcome</button>
        {reference && originalSave.current?.key === reference.key && !saved && <button className="btn-ghost" disabled={!canWrite || busy} onClick={retryOriginalSave}>Retry original manual save</button>}
      </div>}
      {saved && readState === "failed" && <button className="btn-ghost" disabled={!canWrite || busy} onClick={() => {
        if (busyRef.current) return;
        busyRef.current = true; setBusy(true);
        void readSaved(saved.report).finally(() => { busyRef.current = false; if (active.current) setBusy(false); });
      }}>Retry loading saved report</button>}
      {!open ? <button className="btn-primary" disabled={!canWrite || busy || unresolved || (!!saved && readState !== "ready")} onClick={beginDraft}>+ New expense report (manual)</button> : !saved && (
        <div className="card space-y-4">
          <h2 className="text-sm font-semibold text-slate-600">New expense report</h2>
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex-1 min-w-[200px]">
              <label className="label" htmlFor="title">Title</label>
              <input id="title" className="input" value={title} maxLength={200} disabled={frozen} onChange={(e) => { if (!busyRef.current && !unresolved) setTitle(e.target.value); }} placeholder="e.g. Berlin conference" />
            </div>
            <div>
              <label className="label" htmlFor="currency">Currency</label>
              <input id="currency" className="input w-24 uppercase" maxLength={3} value={currency} disabled={frozen} onChange={(e) => { if (!busyRef.current && !unresolved) setCurrency(e.target.value.toUpperCase()); }} />
            </div>
          </div>
          <div className="overflow-x-auto rounded-lg border border-slate-200">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500"><tr>
                <th className="px-2 py-2 w-32">Date</th><th className="px-2 py-2 w-32">Category</th><th className="px-2 py-2">Description</th><th className="px-2 py-2">Business purpose</th><th className="px-2 py-2 w-28">Amount</th><th className="px-2 py-2 w-24">VAT</th><th className="px-2 py-2">Remove</th>
              </tr></thead>
              <tbody>{items.map((it, i) => <tr key={i} className="border-t border-slate-100">
                <td className="px-2 py-1"><input aria-label={`Item ${i + 1} spend date`} type="date" className="input" value={it.spend_date} disabled={frozen} onChange={(e) => setItem(i, { spend_date: e.target.value })} /></td>
                <td className="px-2 py-1"><select aria-label={`Item ${i + 1} category`} className="input" value={it.category} disabled={frozen} onChange={(e) => setItem(i, { category: e.target.value as ExpenseItemInput["category"] })}>{EXPENSE_CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}</select></td>
                <td className="px-2 py-1"><input aria-label={`Item ${i + 1} description`} className="input" value={it.description} maxLength={300} disabled={frozen} onChange={(e) => setItem(i, { description: e.target.value })} /></td>
                <td className="px-2 py-1"><input className="input" placeholder="Why was this spent?" aria-label={`Item ${i + 1} business purpose`} value={it.comment ?? ""} maxLength={1000} disabled={frozen} onChange={(e) => setItem(i, { comment: e.target.value })} /></td>
                <td className="px-2 py-1"><input aria-label={`Item ${i + 1} amount`} className="input" inputMode="decimal" maxLength={32} value={it.amount} disabled={frozen} onChange={(e) => setItem(i, { amount: e.target.value })} /></td>
                <td className="px-2 py-1"><input aria-label={`Item ${i + 1} VAT`} className="input" inputMode="decimal" maxLength={32} value={it.vat_amount} disabled={frozen} onChange={(e) => setItem(i, { vat_amount: e.target.value })} /></td>
                <td className="px-2 py-1 text-right">{items.length > 1 && <button className="text-rose-500 hover:underline" aria-label={`Remove expense ${i + 1}`} disabled={frozen} onClick={() => { if (!busyRef.current && !unresolved) setItems((current) => current.filter((_, idx) => idx !== i)); }}>×</button>}</td>
              </tr>)}</tbody>
            </table>
          </div>
          <button className="btn-ghost" disabled={frozen} onClick={() => { if (!busyRef.current && !unresolved) setItems((current) => [...current, emptyItem()]); }}>+ Add expense</button>
          <p className="text-xs text-text-secondary">Each expense needs a business purpose and an attached receipt before the report can be submitted — open the saved draft to attach receipts. Saving creates a draft only.</p>
          <div className="flex items-center justify-between">
            <span className="text-sm text-slate-500">Total: <span className="font-semibold text-slate-700">{total === null ? "—" : decimalMoney(total, currency)}</span></span>
            <div className="flex gap-2">
              <button className="btn-ghost" disabled={busy} onClick={() => { if (!busyRef.current) setOpen(false); }}>Cancel</button>
              <button className="btn-primary" disabled={!canWrite || busy || unresolved || !title.trim() || items.some((it) => !it.description.trim())} onClick={saveDraft}>{busy ? "Saving…" : "Save draft"}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// Self-service payout bank details (IBAN/BIC), used when an expense reimbursement
// batch is exported as a SEPA credit transfer. Collapsed by default.
function BankDetailsCard() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [iban, setIban] = useState("");
  const [bic, setBic] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const me = useQuery<{ user: { iban: string | null; bic: string | null } }>({
    queryKey: ["me", "bank"],
    queryFn: async () => {
      const d = (await api.get("/auth/me")).data;
      setIban(d.user.iban ?? "");
      setBic(d.user.bic ?? "");
      return d;
    },
    enabled: open,
  });

  const save = useMutation({
    mutationFn: async () =>
      (await api.put("/auth/bank-details", { iban: iban || null, bic: bic || null })).data,
    onSuccess: () => {
      setErr(null);
      setMsg("Saved.");
      qc.invalidateQueries({ queryKey: ["me", "bank"] });
    },
    onError: (e) => { setMsg(null); setErr(apiError(e)); },
  });

  const current = me.data?.user;
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <button className="flex w-full items-center justify-between text-left" onClick={() => setOpen((o) => !o)}>
        <div>
          <div className="text-sm font-semibold text-slate-700">My reimbursement bank details</div>
          <div className="text-xs text-text-secondary">
            {current?.iban ? `IBAN on file · ${current.iban.slice(0, 8)}…` : "Add an IBAN so your expenses can be paid by SEPA transfer"}
          </div>
        </div>
        <span className="text-slate-400">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="mt-3 space-y-2">
          <div className="grid gap-2 sm:grid-cols-2">
            <input className="input py-1 font-mono text-sm" placeholder="IBAN" aria-label="IBAN" value={iban} onChange={(e) => setIban(e.target.value)} />
            <input className="input py-1 text-sm" placeholder="BIC (optional)" aria-label="BIC (optional)" value={bic} onChange={(e) => setBic(e.target.value)} />
          </div>
          {err && <div className="text-xs text-rose-600">{err}</div>}
          {msg && <div className="text-xs text-emerald-600">{msg}</div>}
          <div className="flex justify-end">
            <button className="btn-primary" disabled={save.isPending} onClick={() => save.mutate()}>Save bank details</button>
          </div>
        </div>
      )}
    </div>
  );
}
