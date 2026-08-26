import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { api, apiError } from "../lib/api";
import type { IssuerProfile } from "../lib/types";
import { isAdminOrAbove } from "../lib/roles";

const FIELDS: { key: keyof IssuerProfile; label: string; required?: boolean; half?: boolean }[] = [
  { key: "name", label: "Entity label (how you refer to it internally)", half: true },
  { key: "legal_name", label: "Legal company name", required: true, half: true },
  { key: "trade_name", label: "Trade name (optional)" },
  { key: "vat_number", label: "VAT number", required: true, half: true },
  { key: "registration_number", label: "Company registration no.", half: true },
  { key: "address_line1", label: "Address line 1", required: true },
  { key: "address_line2", label: "Address line 2" },
  { key: "postal_code", label: "Postal code", required: true, half: true },
  { key: "city", label: "City", required: true, half: true },
  { key: "country", label: "Country (ISO, e.g. NL)", required: true, half: true },
  { key: "default_currency", label: "Default currency", half: true },
  { key: "email", label: "Billing email", half: true },
  { key: "phone", label: "Phone", half: true },
  { key: "iban", label: "IBAN", half: true },
  { key: "bic", label: "BIC", half: true },
  { key: "invoice_prefix", label: "Invoice number prefix", half: true },
  { key: "credit_note_prefix", label: "Credit-note prefix", half: true },
  { key: "default_penalty_rate", label: "Default late-payment interest (% p.a.)", half: true },
];

const registryKey = ["issuer-registry"];

export default function Issuer() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const isOwner = isAdminOrAbove(user);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const registry = useQuery<IssuerProfile[]>({
    queryKey: registryKey,
    queryFn: async () => (await api.get("/issuer/registry")).data,
  });

  // Default the editor to the org's default entity once the list loads.
  useEffect(() => {
    if (!selectedId && registry.data && registry.data.length > 0) {
      const def = registry.data.find((p) => p.is_default) ?? registry.data[0];
      setSelectedId(def.id);
    }
  }, [registry.data, selectedId]);

  const selected = useMemo(
    () => registry.data?.find((p) => p.id === selectedId) ?? null,
    [registry.data, selectedId],
  );

  const create = useMutation({
    mutationFn: async () => (await api.post("/issuer/registry", { legal_name: "New entity" })).data as IssuerProfile,
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: registryKey });
      qc.invalidateQueries({ queryKey: ["issuer"] });
      setSelectedId(p.id);
    },
  });
  const setDefault = useMutation({
    mutationFn: async (id: string) => (await api.post(`/issuer/registry/${id}/default`, {})).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: registryKey });
      qc.invalidateQueries({ queryKey: ["issuer"] });
    },
  });
  const remove = useMutation({
    mutationFn: async (id: string) => api.delete(`/issuer/registry/${id}`),
    onSuccess: (_r, id) => {
      qc.invalidateQueries({ queryKey: registryKey });
      qc.invalidateQueries({ queryKey: ["issuer"] });
      if (selectedId === id) setSelectedId(null);
    },
  });

  const multi = (registry.data?.length ?? 0) > 1;

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Issuer companies</h1>
        <p className="text-sm text-slate-500">
          Each legal entity you invoice from — its details identify the seller on every invoice
          (EN 16931 / EU VAT Directive Art. 226) and it keeps its own gap-free numbering series.
        </p>
      </div>

      {!isOwner && (
        <div className="rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-500">
          Only the workspace owner can edit company details.
        </div>
      )}
      {remove.isError && (
        <div className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-600">{apiError(remove.error)}</div>
      )}

      {/* Registry: the entities, with the editor targeting the selected one. */}
      <div className="card space-y-2 p-0">
        {registry.data?.map((p) => {
          const active = p.id === selectedId;
          return (
            <div
              key={p.id}
              className={`flex items-center justify-between gap-3 border-b border-slate-100 px-4 py-3 last:border-0 ${
                active ? "bg-brand-50/50" : ""
              }`}
            >
              <button className="min-w-0 flex-1 text-left" onClick={() => setSelectedId(p.id)}>
                <div className="flex items-center gap-2">
                  <span className="truncate font-medium">
                    {p.name || p.legal_name || <span className="italic text-slate-400">Unnamed entity</span>}
                  </span>
                  {p.is_default && <span className="badge bg-emerald-100 text-emerald-700">Default</span>}
                  {!p.is_complete && <span className="badge bg-amber-100 text-amber-700">Incomplete</span>}
                </div>
                <div className="truncate text-xs text-slate-400">
                  {p.vat_number ? `${p.vat_number} · ` : ""}series {p.invoice_prefix}
                </div>
              </button>
              {isOwner && (
                <div className="flex shrink-0 items-center gap-2 text-xs">
                  {!p.is_default && (
                    <button
                      className="text-brand-600 hover:underline disabled:text-slate-300"
                      disabled={setDefault.isPending}
                      onClick={() => setDefault.mutate(p.id)}
                    >
                      Set default
                    </button>
                  )}
                  {!p.is_default && (
                    <button
                      className="text-rose-500 hover:underline disabled:text-slate-300"
                      disabled={remove.isPending}
                      title="Delete this entity (only if it has never issued an invoice)"
                      onClick={() => {
                        if (window.confirm(`Delete "${p.name || p.legal_name}"? This can't be undone.`))
                          remove.mutate(p.id);
                      }}
                    >
                      Delete
                    </button>
                  )}
                </div>
              )}
            </div>
          );
        })}
        {isOwner && (
          <div className="px-4 py-3">
            <button className="btn-ghost" disabled={create.isPending} onClick={() => create.mutate()}>
              {create.isPending ? "Adding…" : "+ Add issuer company"}
            </button>
          </div>
        )}
      </div>

      {selected && (
        <IssuerEditor key={selected.id} profile={selected} isOwner={isOwner} multi={multi} />
      )}
    </div>
  );
}

function IssuerEditor({
  profile,
  isOwner,
  multi,
}: {
  profile: IssuerProfile;
  isOwner: boolean;
  multi: boolean;
}) {
  const qc = useQueryClient();
  const [form, setForm] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    const init: Record<string, string> = {};
    FIELDS.forEach((f) => (init[f.key] = (profile as any)[f.key] ?? ""));
    init.notes = profile.notes ?? "";
    init.payment_instructions = profile.payment_instructions ?? "";
    setForm(init);
  }, [profile]);

  const save = useMutation({
    mutationFn: async () => {
      const payload: Record<string, unknown> = { ...form };
      // An empty penalty field means "no default" — send null, not "" (Decimal).
      payload.default_penalty_rate = form.default_penalty_rate?.trim() ? form.default_penalty_rate : null;
      return (await api.put(`/issuer/registry/${profile.id}`, payload)).data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: registryKey });
      qc.invalidateQueries({ queryKey: ["issuer"] });
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    },
  });

  return (
    <div className="space-y-4">
      <div
        className={`rounded-lg px-3 py-2 text-sm ${
          profile.is_complete ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"
        }`}
      >
        {profile.is_complete ? (
          <>
            ✓ This entity is complete — you can{" "}
            <Link to="/issue" className="font-medium underline">issue invoices</Link>
            {multi ? " and pick it when creating an invoice." : "."}
          </>
        ) : (
          <>Missing required fields: {profile.missing_fields.join(", ")}</>
        )}
      </div>

      {save.isError && (
        <div className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-600">{apiError(save.error)}</div>
      )}

      <div className="card grid grid-cols-1 gap-4 sm:grid-cols-2">
        {FIELDS.map((f) => (
          <div key={f.key} className={f.half ? "" : "sm:col-span-2"}>
            <label className="label" htmlFor={`issuer-${f.key}`}>
              {f.label} {f.required && <span className="text-rose-500">*</span>}
            </label>
            <input
              id={`issuer-${f.key}`}
              className="input"
              value={form[f.key] ?? ""}
              disabled={!isOwner}
              onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
            />
          </div>
        ))}
        <div className="sm:col-span-2">
          <label className="label" htmlFor="payment-instructions-shown-on-the-invoice">Payment instructions (shown on the invoice)</label>
          <textarea id="payment-instructions-shown-on-the-invoice"
            className="input"
            rows={2}
            placeholder="e.g. Payment within 14 days; quote the invoice number as reference."
            value={form["payment_instructions"] ?? ""}
            disabled={!isOwner}
            onChange={(e) => setForm({ ...form, payment_instructions: e.target.value })}
          />
        </div>
        <div className="sm:col-span-2">
          <label className="label" htmlFor="footer-notes">Footer / notes</label>
          <textarea id="footer-notes"
            className="input"
            rows={2}
            value={form["notes"] ?? ""}
            disabled={!isOwner}
            onChange={(e) => setForm({ ...form, notes: e.target.value })}
          />
        </div>
      </div>

      <div className="flex items-center gap-3">
        <button className="btn-primary" disabled={!isOwner || save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? "Saving…" : "Save company details"}
        </button>
        {saved && <span className="text-sm text-emerald-600">Saved.</span>}
      </div>
    </div>
  );
}
