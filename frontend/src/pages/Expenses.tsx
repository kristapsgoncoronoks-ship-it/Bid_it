import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { KpiCard } from "../components/KpiCard";
import { ModuleInactive } from "../components/ModuleGate";
import { useAuth } from "../auth/AuthContext";
import { api, apiError } from "../lib/api";
import { EXPENSE_STATUS_STYLES, money, shortDate } from "../lib/format";
import { isAdminOrAbove } from "../lib/roles";
import { useModules } from "../lib/useModules";
import {
  EXPENSE_CATEGORIES,
  type ExpenseItemInput,
  type ExpenseReport,
  type ExpenseSummary,
  type ExpenseTransaction,
} from "../lib/types";

const emptyItem = (): ExpenseItemInput => ({
  spend_date: new Date().toISOString().slice(0, 10),
  category: "travel",
  description: "",
  amount: "0",
  vat_amount: "0",
  payment_method: "personal",
});

export default function Expenses() {
  const { user } = useAuth();
  const isManager = isAdminOrAbove(user);
  const modules = useModules();
  const enabled = modules.isEnabled("expenses");

  const summary = useQuery<ExpenseSummary>({ queryKey: ["expenses", "summary"], queryFn: async () => (await api.get("/expenses/summary")).data, enabled });
  const mine = useQuery<{ items: ExpenseReport[]; total: number }>({ queryKey: ["expenses", "mine"], queryFn: async () => (await api.get("/expenses?mine=true")).data, enabled });
  const pending = useQuery<{ items: ExpenseReport[]; total: number }>({
    queryKey: ["expenses", "pending"],
    queryFn: async () => (await api.get("/expenses?status=submitted")).data,
    enabled: enabled && isManager,
  });

  if (modules.data && !enabled) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-semibold tracking-tight">Expenses</h1>
        <ModuleInactive name="employee expenses" />
      </div>
    );
  }

  const s = summary.data;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">Expenses</h1>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <KpiCard label="Awaiting reimbursement" value={s ? money(s.my_reimbursable) : "—"} accent="emerald" sub="approved · mine" />
        <KpiCard label="Reclaimable VAT" value={s ? money(s.reclaimable_vat) : "—"} accent="amber" sub="on my expenses" />
        <KpiCard label="Submitted" value={s ? String(s.my_submitted) : "—"} sub="mine, awaiting review" />
        {isManager && <KpiCard label="To approve" value={s ? String(s.pending_approvals) : "—"} accent="rose" sub="team, awaiting me" />}
      </div>

      <AvailableExpenses enabled={!!enabled} />

      <NewReport />

      {isManager && (pending.data?.total ?? 0) > 0 && (
        <ReportTable title={`Awaiting my approval (${pending.data!.total})`} rows={pending.data!.items} showEmployee />
      )}

      <ReportTable title={`My reports (${mine.data?.total ?? 0})`} rows={mine.data?.items ?? []} />
    </div>
  );
}

function AvailableExpenses({ enabled }: { enabled: boolean }) {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [title, setTitle] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const txns = useQuery<ExpenseTransaction[]>({
    queryKey: ["expenses", "transactions"],
    queryFn: async () => (await api.get("/expenses/transactions")).data,
    enabled,
  });

  const importStmt = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return (await api.post("/expenses/import/bank-statement", form)).data as { imported: number; method: string };
    },
    onSuccess: (d) => { setMsg(`Imported ${d.imported} transaction(s) (${d.method}).`); setError(null); qc.invalidateQueries({ queryKey: ["expenses", "transactions"] }); },
    onError: (e) => setError(apiError(e)),
  });
  const del = useMutation({
    mutationFn: async (id: string) => api.delete(`/expenses/transactions/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["expenses", "transactions"] }),
  });
  const createFrom = useMutation({
    mutationFn: async () => (await api.post("/expenses", { title: title || "New report", transaction_ids: [...selected] })).data,
    onSuccess: () => {
      setSelected(new Set()); setTitle(""); setMsg("Report created from selected transactions.");
      qc.invalidateQueries({ queryKey: ["expenses"] });
    },
    onError: (e) => setError(apiError(e)),
  });

  const toggle = (id: string) => setSelected((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const rows = txns.data ?? [];
  const selectedTotal = rows.filter((t) => selected.has(t.id)).reduce((sum, t) => sum + Number(t.amount), 0);

  return (
    <div className="card space-y-3">
      <input ref={fileRef} type="file" accept=".pdf,.csv" className="hidden"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) importStmt.mutate(f); e.target.value = ""; }} />
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-slate-600">Available expenses{rows.length ? ` (${rows.length})` : ""}</h2>
          <p className="text-xs text-slate-500">Card / bank transactions to add to a report.</p>
        </div>
        <button className="btn-ghost" disabled={importStmt.isPending} onClick={() => fileRef.current?.click()}>
          {importStmt.isPending ? "Reading…" : "Import bank statement"}
        </button>
      </div>
      {msg && <div className="rounded-lg bg-sky-50 px-3 py-2 text-sm text-sky-700">{msg}</div>}
      {error && <div className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-600">{error}</div>}

      {rows.length === 0 ? (
        <div className="py-4 text-center text-sm text-slate-400">No available transactions. Import a statement to get started.</div>
      ) : (
        <>
          <div className="overflow-x-auto rounded-lg border border-slate-200">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
                <tr>
                  <th className="px-3 py-2 w-8"></th>
                  <th className="px-3 py-2">Date</th>
                  <th className="px-3 py-2">Description</th>
                  <th className="px-3 py-2 text-right">Amount</th>
                  <th className="px-3 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((t) => (
                  <tr key={t.id} className="border-t border-slate-100">
                    <td className="px-3 py-2"><input type="checkbox" checked={selected.has(t.id)} onChange={() => toggle(t.id)} /></td>
                    <td className="px-3 py-2 text-slate-500">{shortDate(t.txn_date)}</td>
                    <td className="px-3 py-2">{t.description}</td>
                    <td className="px-3 py-2 text-right font-medium">{money(t.amount, t.currency)}</td>
                    <td className="px-3 py-2 text-right"><button className="text-rose-500 hover:underline" onClick={() => del.mutate(t.id)}>remove</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {selected.size > 0 && (
            <div className="flex flex-wrap items-end gap-3">
              <div className="flex-1 min-w-[200px]">
                <label className="label">Report title</label>
                <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. May card expenses" />
              </div>
              <button className="btn-primary" disabled={createFrom.isPending} onClick={() => createFrom.mutate()}>
                Create report from {selected.size} · {money(selectedTotal)}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function ReportTable({ title, rows, showEmployee }: { title: string; rows: ExpenseReport[]; showEmployee?: boolean }) {
  return (
    <div>
      <h2 className="mb-2 text-sm font-semibold text-slate-600">{title}</h2>
      <div className="card overflow-x-auto p-0">
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
            {rows.map((r) => (
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
            {rows.length === 0 && (
              <tr><td colSpan={showEmployee ? 6 : 5} className="px-4 py-8 text-center text-slate-400">Nothing here.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function NewReport() {
  const qc = useQueryClient();
  const [title, setTitle] = useState("");
  const [currency, setCurrency] = useState("EUR");
  const [items, setItems] = useState<ExpenseItemInput[]>([emptyItem()]);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  const create = useMutation({
    mutationFn: async () => (await api.post("/expenses", { title, currency, items })).data,
    onSuccess: () => {
      setTitle(""); setItems([emptyItem()]); setError(null); setOpen(false);
      qc.invalidateQueries({ queryKey: ["expenses"] });
    },
    onError: (e) => setError(apiError(e)),
  });

  const setItem = (i: number, patch: Partial<ExpenseItemInput>) => setItems(items.map((it, idx) => (idx === i ? { ...it, ...patch } : it)));
  const total = items.reduce((s, it) => s + Number(it.amount || 0), 0);

  if (!open) return <button className="btn-primary" onClick={() => setOpen(true)}>+ New expense report (manual)</button>;

  return (
    <div className="card space-y-4">
      <h2 className="text-sm font-semibold text-slate-600">New expense report</h2>
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex-1 min-w-[200px]">
          <label className="label">Title</label>
          <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Berlin conference" />
        </div>
        <div>
          <label className="label">Currency</label>
          <input className="input w-24 uppercase" maxLength={3} value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase())} />
        </div>
      </div>

      <div className="overflow-x-auto rounded-lg border border-slate-200">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
            <tr>
              <th className="px-2 py-2 w-32">Date</th>
              <th className="px-2 py-2 w-32">Category</th>
              <th className="px-2 py-2">Description</th>
              <th className="px-2 py-2 w-28">Amount</th>
              <th className="px-2 py-2 w-24">VAT</th>
              <th className="px-2 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {items.map((it, i) => (
              <tr key={i} className="border-t border-slate-100">
                <td className="px-2 py-1"><input type="date" className="input" value={it.spend_date} onChange={(e) => setItem(i, { spend_date: e.target.value })} /></td>
                <td className="px-2 py-1">
                  <select className="input" value={it.category} onChange={(e) => setItem(i, { category: e.target.value as ExpenseItemInput["category"] })}>
                    {EXPENSE_CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
                  </select>
                </td>
                <td className="px-2 py-1"><input className="input" value={it.description} onChange={(e) => setItem(i, { description: e.target.value })} /></td>
                <td className="px-2 py-1"><input className="input" value={it.amount} onChange={(e) => setItem(i, { amount: e.target.value })} /></td>
                <td className="px-2 py-1"><input className="input" value={it.vat_amount} onChange={(e) => setItem(i, { vat_amount: e.target.value })} /></td>
                <td className="px-2 py-1 text-right">
                  {items.length > 1 && <button className="text-rose-500 hover:underline" onClick={() => setItems(items.filter((_, idx) => idx !== i))}>×</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <button className="btn-ghost" onClick={() => setItems([...items, emptyItem()])}>+ Add expense</button>

      {error && <div className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-600">{error}</div>}

      <div className="flex items-center justify-between">
        <span className="text-sm text-slate-500">Total: <span className="font-semibold text-slate-700">{money(total, currency)}</span></span>
        <div className="flex gap-2">
          <button className="btn-ghost" onClick={() => setOpen(false)}>Cancel</button>
          <button className="btn-primary" disabled={create.isPending || !title || items.some((it) => !it.description)} onClick={() => create.mutate()}>
            {create.isPending ? "Saving…" : "Save draft"}
          </button>
        </div>
      </div>
    </div>
  );
}
