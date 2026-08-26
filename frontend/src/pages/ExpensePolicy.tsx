import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useToast } from "../components/Toast";
import { api, apiError } from "../lib/api";
import {
  EXPENSE_CATEGORIES,
  EXPENSE_POLICY_RULES,
  type ExpenseApprovalPolicy,
  type ExpensePolicy,
} from "../lib/types";

const RULE_LABELS: Record<string, string> = {
  over_item_max: "Over per-item maximum",
  over_category_cap: "Over category cap",
  missing_receipt: "Missing receipt",
  out_of_policy_category: "Out-of-policy category",
  unsupported_currency: "Unsupported currency",
  mileage_rate: "Mileage-rate mismatch",
  missing_business_purpose: "Missing business purpose",
  late_submission: "Late submission",
  weekend_spend: "Weekend spend",
  duplicate_receipt: "Duplicate receipt",
  duplicate_amount_date_merchant: "Duplicate amount/date/merchant",
};

export default function ExpensePolicyPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const { data } = useQuery<ExpensePolicy>({
    queryKey: ["expense-policy"],
    queryFn: async () => (await api.get("/expenses/policy")).data,
  });

  const [f, setF] = useState<ExpensePolicy | null>(null);
  useEffect(() => { if (data) setF(data); }, [data]);

  const save = useMutation({
    mutationFn: async (p: ExpensePolicy) =>
      (await api.put("/expenses/policy", {
        active: p.active,
        max_item_amount: p.max_item_amount || null,
        receipt_required_over: p.receipt_required_over || null,
        category_caps: p.category_caps,
        allowed_categories: p.allowed_categories,
        allowed_currencies: p.allowed_currencies,
        warn_weekend: p.warn_weekend,
        duplicate_detection: p.duplicate_detection,
        mileage_rate: p.mileage_rate || null,
        mileage_rate_tolerance: p.mileage_rate_tolerance || null,
        require_purpose_over: p.require_purpose_over || null,
        late_submission_days: p.late_submission_days,
        blocking_rules: p.blocking_rules,
      })).data,
    onSuccess: () => { toast.success("Policy saved"); qc.invalidateQueries({ queryKey: ["expense-policy"] }); },
    onError: (e) => toast.error(apiError(e)),
  });

  if (!f) return <div className="text-slate-400">Loading…</div>;
  const set = (patch: Partial<ExpensePolicy>) => setF({ ...f, ...patch });
  const toggleIn = (list: string[], v: string) =>
    list.includes(v) ? list.filter((x) => x !== v) : [...list, v];

  const inp = "input py-1 text-sm";
  const num = (v: string | null) => v ?? "";

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Expense policy</h1>
        <p className="text-slate-500">
          Rules run when a report is opened and at submission. Findings are <strong>advisory</strong> — an
          expense is flagged for review, never auto-rejected — unless you mark a rule as blocking below.
        </p>
      </div>

      <div className="card space-y-4">
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={f.active} onChange={(e) => set({ active: e.target.checked })} />
          Policy active
        </label>

        <div className="grid gap-4 md:grid-cols-3">
          <L label="Max per-item amount"><input className={inp} inputMode="decimal" value={num(f.max_item_amount)} onChange={(e) => set({ max_item_amount: e.target.value })} /></L>
          <L label="Receipt required over"><input className={inp} inputMode="decimal" value={num(f.receipt_required_over)} onChange={(e) => set({ receipt_required_over: e.target.value })} /></L>
          <L label="Business purpose required over"><input className={inp} inputMode="decimal" value={num(f.require_purpose_over)} onChange={(e) => set({ require_purpose_over: e.target.value })} /></L>
          <L label="Sanctioned mileage rate"><input className={inp} inputMode="decimal" value={num(f.mileage_rate)} onChange={(e) => set({ mileage_rate: e.target.value })} /></L>
          <L label="Mileage rate ± tolerance"><input className={inp} inputMode="decimal" value={num(f.mileage_rate_tolerance)} onChange={(e) => set({ mileage_rate_tolerance: e.target.value })} /></L>
          <L label="Late submission after (days)"><input className={inp} inputMode="numeric" value={f.late_submission_days ?? ""} onChange={(e) => set({ late_submission_days: e.target.value ? Number(e.target.value) : null })} /></L>
        </div>

        <div className="flex flex-wrap gap-6">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={f.warn_weekend} onChange={(e) => set({ warn_weekend: e.target.checked })} /> Flag weekend spend
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={f.duplicate_detection} onChange={(e) => set({ duplicate_detection: e.target.checked })} /> Detect duplicates
          </label>
        </div>

        <L label="Allowed categories (unchecked = flag if used)">
          <div className="flex flex-wrap gap-2">
            {EXPENSE_CATEGORIES.map((c) => (
              <label key={c} className="flex items-center gap-1 text-sm">
                <input type="checkbox" checked={f.allowed_categories.length === 0 || f.allowed_categories.includes(c)}
                  onChange={() => set({ allowed_categories: toggleIn(f.allowed_categories.length ? f.allowed_categories : [...EXPENSE_CATEGORIES], c) })} />
                {c}
              </label>
            ))}
          </div>
        </L>

        <L label="Allowed currencies (comma-separated ISO codes; empty = all)">
          <input className={inp} placeholder="EUR, USD, GBP" value={f.allowed_currencies.join(", ")}
            onChange={(e) => set({ allowed_currencies: e.target.value.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean) })} />
        </L>

        <L label="Per-category caps">
          <div className="grid gap-2 md:grid-cols-3">
            {EXPENSE_CATEGORIES.map((c) => (
              <div key={c} className="flex items-center gap-2">
                <span className="w-24 text-xs text-slate-500">{c}</span>
                <input className={inp} inputMode="decimal" value={f.category_caps[c] ?? ""}
                  onChange={(e) => {
                    const caps = { ...f.category_caps };
                    if (e.target.value) caps[c] = e.target.value; else delete caps[c];
                    set({ category_caps: caps });
                  }} />
              </div>
            ))}
          </div>
        </L>

        <div>
          <div className="text-sm font-medium text-slate-600">Blocking rules</div>
          <p className="mb-2 text-xs text-slate-400">Checked rules HARD-BLOCK submission. Everything else only warns.</p>
          <div className="grid gap-1 md:grid-cols-2">
            {EXPENSE_POLICY_RULES.map((code) => (
              <label key={code} className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={f.blocking_rules.includes(code)}
                  onChange={() => set({ blocking_rules: toggleIn(f.blocking_rules, code) })} />
                {RULE_LABELS[code] ?? code}
              </label>
            ))}
          </div>
        </div>

        <div className="flex justify-end">
          <button className="btn-primary" disabled={save.isPending} onClick={() => save.mutate(f)}>Save policy</button>
        </div>
      </div>

      <ApprovalRouting />
    </div>
  );
}

// Multi-step approval routing: ordered approver chains chosen by amount threshold.
function ApprovalRouting() {
  const qc = useQueryClient();
  const toast = useToast();
  const [name, setName] = useState("");
  const [minAmount, setMinAmount] = useState("");
  const [picked, setPicked] = useState<string[]>([]);

  const policies = useQuery<ExpenseApprovalPolicy[]>({
    queryKey: ["expense-approval-policies"],
    queryFn: async () => (await api.get("/expenses/approval-policies")).data,
  });
  const members = useQuery<{ id: string; email: string; name: string; is_expense_approver?: boolean }[]>({
    queryKey: ["team", "members"],
    queryFn: async () => (await api.get("/team/members")).data,
  });
  const approvers = (members.data ?? []).filter((m) => m.is_expense_approver);
  const nameOf = (uid: string) => approvers.find((m) => m.id === uid)?.name ?? uid.slice(0, 8);

  const invalidate = () => qc.invalidateQueries({ queryKey: ["expense-approval-policies"] });
  const create = useMutation({
    mutationFn: async () =>
      (await api.post("/expenses/approval-policies", {
        name,
        min_amount: minAmount || null,
        approver_ids: picked,
      })).data,
    onSuccess: () => { setName(""); setMinAmount(""); setPicked([]); invalidate(); },
    onError: (e) => toast.error(apiError(e)),
  });
  const del = useMutation({
    mutationFn: async (id: string) => api.delete(`/expenses/approval-policies/${id}`),
    onSuccess: invalidate,
    onError: (e) => toast.error(apiError(e)),
  });
  const toggle = (uid: string) =>
    setPicked((p) => (p.includes(uid) ? p.filter((x) => x !== uid) : [...p, uid]));

  return (
    <div className="card space-y-4">
      <div>
        <h2 className="text-sm font-semibold text-slate-600">Approval routing</h2>
        <p className="text-xs text-slate-400">
          Ordered approver chains, selected by amount. No matching policy ⇒ a single approver decides
          (today's behaviour). Only designated expense approvers can be added to a chain.
        </p>
      </div>

      <div className="space-y-2">
        {(policies.data ?? []).map((p) => (
          <div key={p.id} className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm">
            <span className="font-medium">{p.name}</span>
            {!p.active && <span className="badge bg-slate-100 text-slate-400">inactive</span>}
            <span className="text-xs text-slate-400">
              {p.min_amount ? `≥ ${p.min_amount} EUR · ` : ""}
              {p.approver_ids.map(nameOf).join(" → ") || "any approver"}
              {p.finance_final ? " → finance" : ""}
            </span>
            <button className="ml-auto text-xs text-rose-500 hover:underline" onClick={() => del.mutate(p.id)}>remove</button>
          </div>
        ))}
        {(policies.data ?? []).length === 0 && <p className="text-sm text-slate-400">No approval chains — a single approver decides.</p>}
      </div>

      <div className="rounded-lg border border-dashed border-slate-300 p-3 space-y-2">
        <div className="grid gap-2 sm:grid-cols-2">
          <input className="input py-1 text-sm" placeholder="Chain name (e.g. Over €1,000)" value={name} onChange={(e) => setName(e.target.value)} />
          <input className="input py-1 text-sm" inputMode="decimal" placeholder="Applies at/above (EUR, blank = all)" value={minAmount} onChange={(e) => setMinAmount(e.target.value)} />
        </div>
        <div>
          <div className="mb-1 text-xs font-medium text-slate-500">Approvers in order (tick to add)</div>
          <div className="flex flex-wrap gap-2">
            {approvers.map((m) => (
              <label key={m.id} className={`flex items-center gap-1 rounded-lg border px-2 py-1 text-sm ${picked.includes(m.id) ? "border-brand-400 bg-brand-50" : "border-slate-200"}`}>
                <input type="checkbox" checked={picked.includes(m.id)} onChange={() => toggle(m.id)} />
                {m.name}
              </label>
            ))}
            {approvers.length === 0 && <span className="text-xs text-slate-400">Appoint expense approvers on the Team page first.</span>}
          </div>
          {picked.length > 0 && <div className="mt-1 text-xs text-slate-500">Order: {picked.map(nameOf).join(" → ")}</div>}
        </div>
        <div className="flex justify-end">
          <button className="btn-primary" disabled={!name || picked.length === 0 || create.isPending} onClick={() => create.mutate()}>Add chain</button>
        </div>
      </div>
    </div>
  );
}

function L({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block text-sm">{/* a11y-label-exempt: wraps its control via children */}
      <span className="mb-1 block text-xs font-medium text-slate-500">{label}</span>
      {children}
    </label>
  );
}
