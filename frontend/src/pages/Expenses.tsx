import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { KpiCard } from "../components/KpiCard";
import { useAuth } from "../auth/AuthContext";
import { api, apiError } from "../lib/api";
import { EXPENSE_STATUS_STYLES, money, shortDate } from "../lib/format";
import {
  EXPENSE_CATEGORIES,
  type ExpenseItemInput,
  type ExpenseReport,
  type ExpenseSummary,
  type ModuleInfo,
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
  const isManager = user?.role === "owner";
  const modules = useQuery<ModuleInfo[]>({ queryKey: ["modules"], queryFn: async () => (await api.get("/modules")).data });
  const enabled = modules.data?.find((m) => m.key === "expenses")?.enabled;

  const summary = useQuery<ExpenseSummary>({ queryKey: ["expenses", "summary"], queryFn: async () => (await api.get("/expenses/summary")).data, enabled: !!enabled });
  const mine = useQuery<{ items: ExpenseReport[]; total: number }>({ queryKey: ["expenses", "mine"], queryFn: async () => (await api.get("/expenses?mine=true")).data, enabled: !!enabled });
  const pending = useQuery<{ items: ExpenseReport[]; total: number }>({
    queryKey: ["expenses", "pending"],
    queryFn: async () => (await api.get("/expenses?status=submitted")).data,
    enabled: !!enabled && isManager,
  });

  if (modules.data && !enabled) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-semibold tracking-tight">Expenses</h1>
        <div className="card text-sm text-slate-600">
          The employee expenses module isn't active. Activate it in{" "}
          <Link to="/settings" className="font-medium underline">Settings → Modules</Link>.
        </div>
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

      <NewReport />

      {isManager && (pending.data?.total ?? 0) > 0 && (
        <ReportTable title={`Awaiting my approval (${pending.data!.total})`} rows={pending.data!.items} showEmployee />
      )}

      <ReportTable title={`My reports (${mine.data?.total ?? 0})`} rows={mine.data?.items ?? []} />
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
  const fileRef = useRef<HTMLInputElement>(null);
  const [title, setTitle] = useState("");
  const [currency, setCurrency] = useState("EUR");
  const [items, setItems] = useState<ExpenseItemInput[]>([emptyItem()]);
  const [error, setError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [open, setOpen] = useState(false);

  const create = useMutation({
    mutationFn: async () => (await api.post("/expenses", { title, currency, items })).data,
    onSuccess: () => {
      setTitle(""); setItems([emptyItem()]); setError(null); setWarnings([]); setOpen(false);
      qc.invalidateQueries({ queryKey: ["expenses"] });
    },
    onError: (e) => setError(apiError(e)),
  });

  const importStmt = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return (await api.post("/expenses/import/bank-statement", form)).data as {
        suggested_items: ExpenseItemInput[]; warnings: string[]; method: string;
      };
    },
    onSuccess: (d, file) => {
      setItems(d.suggested_items.length ? d.suggested_items : [emptyItem()]);
      setWarnings([`Read ${d.suggested_items.length} expense(s) from the statement (${d.method}).`, ...d.warnings]);
      if (!title) setTitle(file.name.replace(/\.[^.]+$/, ""));
      setError(null);
      setOpen(true);
    },
    onError: (e) => { setError(apiError(e)); setOpen(true); },
  });

  const setItem = (i: number, patch: Partial<ExpenseItemInput>) => setItems(items.map((it, idx) => (idx === i ? { ...it, ...patch } : it)));
  const total = items.reduce((s, it) => s + Number(it.amount || 0), 0);

  const hiddenInput = (
    <input
      ref={fileRef}
      type="file"
      accept=".pdf,.csv"
      className="hidden"
      onChange={(e) => { const f = e.target.files?.[0]; if (f) importStmt.mutate(f); e.target.value = ""; }}
    />
  );

  if (!open) {
    return (
      <div className="flex flex-wrap gap-2">
        {hiddenInput}
        <button className="btn-primary" onClick={() => setOpen(true)}>+ New expense report</button>
        <button className="btn-ghost" disabled={importStmt.isPending} onClick={() => fileRef.current?.click()}>
          {importStmt.isPending ? "Reading statement…" : "Import bank statement"}
        </button>
      </div>
    );
  }

  return (
    <div className="card space-y-4">
      {hiddenInput}
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-600">New expense report</h2>
        <button className="text-sm text-brand-600 hover:underline" disabled={importStmt.isPending} onClick={() => fileRef.current?.click()}>
          {importStmt.isPending ? "Reading…" : "Import bank statement (PDF/CSV)"}
        </button>
      </div>
      {warnings.length > 0 && (
        <ul className="rounded-lg bg-sky-50 px-3 py-2 text-sm text-sky-700">
          {warnings.map((w, i) => <li key={i}>• {w}</li>)}
        </ul>
      )}
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
