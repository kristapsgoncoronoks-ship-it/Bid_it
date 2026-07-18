import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { api, apiError } from "../lib/api";
import type { IssuerProfile } from "../lib/types";

const FIELDS: { key: keyof IssuerProfile; label: string; required?: boolean; half?: boolean }[] = [
  { key: "legal_name", label: "Legal company name", required: true },
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
];

export default function Issuer() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const isOwner = user?.role === "owner";
  const [form, setForm] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState(false);

  const profile = useQuery<IssuerProfile>({
    queryKey: ["issuer"],
    queryFn: async () => (await api.get("/issuer")).data,
  });

  useEffect(() => {
    if (profile.data) {
      const init: Record<string, string> = {};
      FIELDS.forEach((f) => (init[f.key] = (profile.data as any)[f.key] ?? ""));
      setForm(init);
    }
  }, [profile.data]);

  const save = useMutation({
    mutationFn: async () => (await api.put("/issuer", form)).data,
    onSuccess: (data) => {
      qc.setQueryData(["issuer"], data);
      qc.invalidateQueries({ queryKey: ["modules"] });
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    },
  });

  const p = profile.data;

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Company registration details</h1>
        <p className="text-sm text-slate-500">
          These identify you as the seller on every issued invoice (EN 16931 / EU VAT Directive
          Art. 226). Complete the required fields to start issuing.
        </p>
      </div>

      {p && (
        <div
          className={`rounded-lg px-3 py-2 text-sm ${
            p.is_complete ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"
          }`}
        >
          {p.is_complete ? (
            <>
              ✓ Your company profile is complete — you can{" "}
              <Link to="/issue" className="font-medium underline">issue invoices</Link>.
            </>
          ) : (
            <>Missing required fields: {p.missing_fields.join(", ")}</>
          )}
        </div>
      )}

      {!isOwner && (
        <div className="rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-500">
          Only the workspace owner can edit company details.
        </div>
      )}
      {save.isError && (
        <div className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-600">{apiError(save.error)}</div>
      )}

      <div className="card grid grid-cols-1 gap-4 sm:grid-cols-2">
        {FIELDS.map((f) => (
          <div key={f.key} className={f.half ? "" : "sm:col-span-2"}>
            <label className="label">
              {f.label} {f.required && <span className="text-rose-500">*</span>}
            </label>
            <input
              className="input"
              value={form[f.key] ?? ""}
              disabled={!isOwner}
              onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
            />
          </div>
        ))}
        <div className="sm:col-span-2">
          <label className="label">Footer / payment notes</label>
          <textarea
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
