import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, apiError } from "../lib/api";
import { money, shortDate } from "../lib/format";
import { useModules } from "../lib/useModules";
import type { Partner, PartnerDetail, PenaltySummary } from "../lib/types";

// The three workflow presets the user configures per partner.
const PRESETS = [
  { key: "contract_acceptance", label: "Contract → Acceptance act → Invoice", requires_contract: true, requires_acceptance: true },
  { key: "acceptance", label: "Acceptance act → Invoice", requires_contract: false, requires_acceptance: true },
  { key: "none", label: "Invoice only (no paperwork)", requires_contract: false, requires_acceptance: false },
] as const;

function presetOf(p: { requires_contract: boolean; requires_acceptance: boolean }) {
  if (p.requires_contract && p.requires_acceptance) return "contract_acceptance";
  if (p.requires_acceptance) return "acceptance";
  return "none";
}

const DOC_LABEL: Record<string, string> = { contract: "Contract", acceptance_act: "Acceptance act" };

export default function Partners() {
  const modules = useModules();
  const [selected, setSelected] = useState<string | null>(null);

  const list = useQuery<Partner[]>({
    queryKey: ["partners"],
    queryFn: async () => (await api.get("/partners")).data,
  });

  if (modules.data && !modules.isEnabled("issuing")) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-semibold tracking-tight">Partners</h1>
        <div className="card text-sm text-slate-600">
          The invoice issuing module isn't active. Activate it in{" "}
          <Link to="/settings" className="font-medium underline">Settings → Modules</Link>.
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Partners</h1>
          <p className="text-sm text-slate-500">
            Customers with a formal pre-invoicing workflow — sign the contract and acceptance act
            before invoices can be issued. Penalty invoices need a signed contract.
          </p>
        </div>
        <Link to="/issue" className="btn-ghost">Back to issuing</Link>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[320px_1fr]">
        <div className="space-y-3">
          <NewPartner onCreated={(id) => { list.refetch(); setSelected(id); }} />
          <div className="card p-0">
            {list.data?.length === 0 && <div className="p-4 text-sm text-slate-400">No partners yet.</div>}
            <ul className="divide-y divide-slate-100">
              {list.data?.map((p) => (
                <li key={p.id}>
                  <button
                    className={`flex w-full items-center justify-between px-4 py-3 text-left hover:bg-slate-50 ${selected === p.id ? "bg-brand-50" : ""}`}
                    onClick={() => setSelected(p.id)}
                  >
                    <span className="font-medium text-slate-700">{p.name}</span>
                    <WorkflowBadge p={p} />
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </div>

        <div>{selected ? <PartnerPanel id={selected} onChanged={() => list.refetch()} /> : (
          <div className="card text-sm text-slate-400">Select a partner to manage its workflow.</div>
        )}</div>
      </div>
    </div>
  );
}

function WorkflowBadge({ p }: { p: Partner }) {
  const preset = presetOf(p);
  if (preset === "none") return <span className="badge bg-slate-100 text-slate-500">Invoice only</span>;
  return <span className="badge bg-indigo-100 text-indigo-700">{preset === "contract_acceptance" ? "Contract + act" : "Acceptance"}</span>;
}

function NewPartner({ onCreated }: { onCreated: (id: string) => void }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [preset, setPreset] = useState<string>("contract_acceptance");
  const [penaltyEnabled, setPenaltyEnabled] = useState(true);
  const [penaltyRate, setPenaltyRate] = useState("12");
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: async () => {
      const pr = PRESETS.find((x) => x.key === preset)!;
      const payload: Record<string, unknown> = {
        name, email: email || null,
        requires_contract: pr.requires_contract, requires_acceptance: pr.requires_acceptance,
        penalty_enabled: penaltyEnabled,
        penalty_rate: penaltyRate.trim() ? penaltyRate : null,
      };
      return (await api.post("/partners", payload)).data as PartnerDetail;
    },
    onSuccess: (p) => { setName(""); setEmail(""); setError(null); onCreated(p.id); },
    onError: (e) => setError(apiError(e)),
  });

  return (
    <div className="card space-y-3">
      <h2 className="text-sm font-semibold text-slate-600">New partner</h2>
      <input className="input" placeholder="Company name" value={name} onChange={(e) => setName(e.target.value)} />
      <input className="input" placeholder="Email (optional)" value={email} onChange={(e) => setEmail(e.target.value)} />
      <div>
        <label className="label" htmlFor="pre-invoicing-workflow">Pre-invoicing workflow</label>
        <select id="pre-invoicing-workflow" className="input" value={preset} onChange={(e) => setPreset(e.target.value)}>
          {PRESETS.map((pr) => <option key={pr.key} value={pr.key}>{pr.label}</option>)}
        </select>
      </div>
      <label className="flex items-center gap-2 text-sm text-slate-600">
        <input type="checkbox" checked={penaltyEnabled} onChange={(e) => setPenaltyEnabled(e.target.checked)} />
        Allow penalty invoices
      </label>
      {penaltyEnabled && (
        <div>
          <label className="label" htmlFor="late-payment-interest-p-a">Late-payment interest (% p.a.)</label>
          <input id="late-payment-interest-p-a" className="input" inputMode="decimal" value={penaltyRate} onChange={(e) => setPenaltyRate(e.target.value)} />
        </div>
      )}
      {error && <div className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-600">{error}</div>}
      <button className="btn-primary w-full" disabled={create.isPending || !name} onClick={() => create.mutate()}>
        {create.isPending ? "Adding…" : "Add partner"}
      </button>
    </div>
  );
}

function PartnerPanel({ id, onChanged }: { id: string; onChanged: () => void }) {
  const qc = useQueryClient();
  const detail = useQuery<PartnerDetail>({
    queryKey: ["partner", id],
    queryFn: async () => (await api.get(`/partners/${id}`)).data,
  });
  const penalty = useQuery<PenaltySummary>({
    queryKey: ["partner-penalty", id],
    queryFn: async () => (await api.get(`/partners/${id}/penalty`)).data,
  });

  const refresh = () => { detail.refetch(); penalty.refetch(); onChanged(); };
  const p = detail.data;
  if (!p) return <div className="card text-sm text-slate-400">Loading…</div>;

  return (
    <div className="space-y-4">
      <div className="card space-y-3">
        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-lg font-semibold text-slate-800">{p.name}</h2>
            {p.email && <div className="text-sm text-slate-400">{p.email}</div>}
          </div>
          <ReadinessPill ready={p.readiness.ready} missing={p.readiness.missing} />
        </div>
        <p className="text-sm text-slate-500">
          {p.readiness.required.length === 0
            ? "No paperwork required — invoices can be issued straight away."
            : `Requires: ${p.readiness.required.map((k) => DOC_LABEL[k]).join(" → ")} before invoicing.`}
        </p>
      </div>

      {/* Documents */}
      <div className="card space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-slate-600">Documents</h3>
          <AddDocument partnerId={id} onAdded={refresh} />
        </div>
        {p.documents.length === 0 && <div className="text-sm text-slate-400">No documents yet.</div>}
        <ul className="divide-y divide-slate-100">
          {p.documents.map((d) => (
            <li key={d.id} className="flex items-center justify-between py-2">
              <div>
                <div className="text-sm font-medium text-slate-700">
                  <span className="badge mr-2 bg-slate-100 text-slate-600">{DOC_LABEL[d.kind]}</span>
                  {d.title}
                </div>
                {d.status === "signed" && (
                  <div className="text-xs text-emerald-600">
                    Signed{d.signed_by ? ` by ${d.signed_by}` : ""}{d.signed_date ? ` · ${shortDate(d.signed_date)}` : ""}
                  </div>
                )}
              </div>
              {d.status === "signed" ? (
                <span className="badge bg-emerald-100 text-emerald-700">Signed</span>
              ) : (
                <button
                  className="btn-primary py-1 text-xs"
                  onClick={async () => {
                    await api.post(`/partners/${id}/documents/${d.id}/sign`, {});
                    qc.invalidateQueries({ queryKey: ["partner", id] });
                    refresh();
                  }}
                >
                  Mark signed
                </button>
              )}
            </li>
          ))}
        </ul>
      </div>

      {/* Penalty invoicing */}
      {penalty.data && <PenaltyCard id={id} pen={penalty.data} onGenerated={refresh} />}
    </div>
  );
}

function ReadinessPill({ ready, missing }: { ready: boolean; missing: string[] }) {
  if (ready) return <span className="badge bg-emerald-100 text-emerald-700">Ready to invoice</span>;
  return (
    <span className="badge bg-amber-100 text-amber-700">
      Awaiting {missing.map((k) => DOC_LABEL[k]).join(" + ")}
    </span>
  );
}

function AddDocument({ partnerId, onAdded }: { partnerId: string; onAdded: () => void }) {
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState("contract");
  const [title, setTitle] = useState("");

  const add = useMutation({
    mutationFn: async () => (await api.post(`/partners/${partnerId}/documents`, { kind, title: title || DOC_LABEL[kind] })).data,
    onSuccess: () => { setOpen(false); setTitle(""); onAdded(); },
  });

  if (!open) return <button className="btn-ghost py-1 text-xs" onClick={() => setOpen(true)}>+ Add document</button>;
  return (
    <div className="flex items-center gap-2">
      <select className="input w-36 py-1" value={kind} onChange={(e) => setKind(e.target.value)}>
        <option value="contract">Contract</option>
        <option value="acceptance_act">Acceptance act</option>
      </select>
      <input className="input w-40 py-1" placeholder="Title" value={title} onChange={(e) => setTitle(e.target.value)} />
      <button className="btn-primary py-1 text-xs" disabled={add.isPending} onClick={() => add.mutate()}>Add</button>
      <button className="text-slate-400 hover:underline" onClick={() => setOpen(false)}>cancel</button>
    </div>
  );
}

function PenaltyCard({ id, pen, onGenerated }: { id: string; pen: PenaltySummary; onGenerated: () => void }) {
  const [note, setNote] = useState<string | null>(null);
  const gen = useMutation({
    mutationFn: async () => (await api.post(`/partners/${id}/penalty-invoice`)).data,
    onSuccess: (inv) => { setNote(`Penalty invoice ${inv.number} generated (${money(inv.total, inv.currency)}).`); onGenerated(); },
    onError: (e) => setNote(apiError(e)),
  });

  return (
    <div className="card space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-600">Penalty invoicing</h3>
        <button
          className="btn-primary py-1 text-xs disabled:opacity-50"
          disabled={!pen.can_generate || gen.isPending}
          onClick={() => { setNote(null); gen.mutate(); }}
        >
          {gen.isPending ? "Generating…" : "Generate penalty invoice"}
        </button>
      </div>

      <div className="grid grid-cols-3 gap-3 text-sm">
        <div>
          <div className="text-xs text-slate-400">Accrued interest</div>
          <div className="font-semibold text-slate-800">{money(pen.total_penalty, pen.currency)}</div>
        </div>
        <div>
          <div className="text-xs text-slate-400">Overdue outstanding</div>
          <div className="font-semibold text-slate-800">{money(pen.total_outstanding, pen.currency)}</div>
        </div>
        <div>
          <div className="text-xs text-slate-400">Max days overdue</div>
          <div className="font-semibold text-slate-800">{pen.max_days_overdue}</div>
        </div>
      </div>

      {!pen.can_generate && pen.blocked_reason && (
        <div className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700">{pen.blocked_reason}</div>
      )}

      {pen.lines.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-left text-slate-400">
              <tr><th className="py-1">Invoice</th><th className="py-1 text-right">Days overdue</th><th className="py-1 text-right">Outstanding</th><th className="py-1 text-right">Interest</th></tr>
            </thead>
            <tbody>
              {pen.lines.map((ln) => (
                <tr key={ln.invoice_id} className="border-t border-slate-100">
                  <td className="py-1 font-medium text-slate-600">{ln.number}</td>
                  <td className="py-1 text-right">{ln.days_overdue}</td>
                  <td className="py-1 text-right">{money(ln.outstanding, pen.currency)}</td>
                  <td className="py-1 text-right text-rose-600">{money(ln.penalty, pen.currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {note && <div className="text-xs text-emerald-600">{note}</div>}
    </div>
  );
}
