import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useToast } from "../components/Toast";
import { api, apiError } from "../lib/api";
import type { Customer } from "../lib/types";

const BLANK = {
  name: "",
  vat_number: "",
  registration_number: "",
  email: "",
  phone: "",
  address_line1: "",
  address_line2: "",
  city: "",
  postal_code: "",
  country: "",
  ship_address_line1: "",
  ship_city: "",
  ship_postal_code: "",
  ship_country: "",
  payment_terms_days: "",
  default_currency: "",
  notes: "",
};

export default function CustomersPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const [editing, setEditing] = useState<string | null>(null); // id | "new" | null

  const customers = useQuery<Customer[]>({
    queryKey: ["customers"],
    queryFn: async () => (await api.get("/customers")).data,
  });

  const del = useMutation({
    mutationFn: async (id: string) => api.delete(`/customers/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["customers"] }),
    onError: (e) => toast.error(apiError(e)),
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Customers</h1>
          <p className="text-slate-500">Reusable billing details for the invoices you issue.</p>
        </div>
        <div className="flex items-center gap-3">
          <Link to="/issue" className="text-sm text-brand-600 hover:underline">Issue →</Link>
          <button className="btn-primary" onClick={() => setEditing("new")}>New customer</button>
        </div>
      </div>

      {editing && (
        <CustomerForm
          id={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
        />
      )}

      <div className="card overflow-x-auto p-0">
        <table className="w-full text-sm">
          <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase text-slate-500">
            <tr>
              <th className="px-4 py-3">Customer</th>
              <th className="px-4 py-3">VAT no.</th>
              <th className="px-4 py-3">City</th>
              <th className="px-4 py-3">Terms</th>
              <th className="px-4 py-3">Currency</th>
              <th className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody>
            {(customers.data ?? []).map((c) => (
              <tr key={c.id} className="border-b border-slate-100 last:border-0">
                <td className="px-4 py-3 font-medium">
                  {c.name}
                  {c.email && <div className="text-xs text-slate-400">{c.email}</div>}
                </td>
                <td className="px-4 py-3 text-slate-500">{c.vat_number || "—"}</td>
                <td className="px-4 py-3 text-slate-500">{c.city || "—"}{c.country ? `, ${c.country}` : ""}</td>
                <td className="px-4 py-3 text-slate-500">{c.payment_terms_days != null ? `${c.payment_terms_days}d` : "—"}</td>
                <td className="px-4 py-3 text-slate-500">{c.default_currency || "—"}</td>
                <td className="px-4 py-3 text-right">
                  <button className="text-brand-600 hover:underline" onClick={() => setEditing(c.id)}>edit</button>
                  <button className="ml-3 text-rose-500 hover:underline" onClick={() => del.mutate(c.id)}>archive</button>
                </td>
              </tr>
            ))}
            {(customers.data ?? []).length === 0 && (
              <tr><td colSpan={6} className="px-4 py-4 text-slate-400">No customers yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function CustomerForm({ id, onClose }: { id: string | null; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const existing = useQuery<Customer>({
    queryKey: ["customer", id],
    queryFn: async () => (await api.get(`/customers/${id}`)).data,
    enabled: !!id,
  });
  const [f, setF] = useState<Record<string, string>>(BLANK);
  const [seeded, setSeeded] = useState(false);
  if (id && existing.data && !seeded) {
    const d = existing.data;
    setF({
      ...BLANK,
      ...Object.fromEntries(Object.keys(BLANK).map((k) => [k, (d as never)[k] ?? ""])),
      payment_terms_days: d.payment_terms_days != null ? String(d.payment_terms_days) : "",
    });
    setSeeded(true);
  }
  const set = (k: string, v: string) => setF((s) => ({ ...s, [k]: v }));

  const save = useMutation({
    mutationFn: async () => {
      const payload: Record<string, unknown> = { ...f };
      payload.payment_terms_days = f.payment_terms_days ? Number(f.payment_terms_days) : null;
      payload.default_currency = f.default_currency || null;
      for (const k of Object.keys(payload)) if (payload[k] === "") payload[k] = null;
      payload.name = f.name;
      return id
        ? (await api.patch(`/customers/${id}`, payload)).data
        : (await api.post("/customers", payload)).data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["customers"] });
      toast.success("Saved");
      onClose();
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const inp = "input py-1 text-sm";
  return (
    <div className="card space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-600">{id ? "Edit customer" : "New customer"}</h2>
        <button className="text-xs text-slate-400 hover:underline" onClick={onClose}>close</button>
      </div>
      <div className="grid gap-2 md:grid-cols-3">
        <L label="Name *"><input className={inp} value={f.name} onChange={(e) => set("name", e.target.value)} /></L>
        <L label="VAT number"><input className={inp} value={f.vat_number} onChange={(e) => set("vat_number", e.target.value)} /></L>
        <L label="Registration no."><input className={inp} value={f.registration_number} onChange={(e) => set("registration_number", e.target.value)} /></L>
        <L label="Email"><input className={inp} value={f.email} onChange={(e) => set("email", e.target.value)} /></L>
        <L label="Phone"><input className={inp} value={f.phone} onChange={(e) => set("phone", e.target.value)} /></L>
        <L label="Payment terms (days)"><input className={inp} inputMode="numeric" value={f.payment_terms_days} onChange={(e) => set("payment_terms_days", e.target.value)} /></L>
      </div>
      <div className="text-xs font-medium text-slate-500">Billing address</div>
      <div className="grid gap-2 md:grid-cols-3">
        <input className={`${inp} md:col-span-2`} placeholder="Address line 1" value={f.address_line1} onChange={(e) => set("address_line1", e.target.value)} />
        <input className={inp} placeholder="Line 2" value={f.address_line2} onChange={(e) => set("address_line2", e.target.value)} />
        <input className={inp} placeholder="City" value={f.city} onChange={(e) => set("city", e.target.value)} />
        <input className={inp} placeholder="Postal code" value={f.postal_code} onChange={(e) => set("postal_code", e.target.value)} />
        <input className={inp} placeholder="Country (ISO)" maxLength={2} value={f.country} onChange={(e) => set("country", e.target.value.toUpperCase())} />
        <input className={inp} placeholder="Default currency" maxLength={3} value={f.default_currency} onChange={(e) => set("default_currency", e.target.value.toUpperCase())} />
      </div>
      <div className="text-xs font-medium text-slate-500">Shipping address (if different)</div>
      <div className="grid gap-2 md:grid-cols-3">
        <input className={`${inp} md:col-span-2`} placeholder="Ship to line 1" value={f.ship_address_line1} onChange={(e) => set("ship_address_line1", e.target.value)} />
        <input className={inp} placeholder="Ship city" value={f.ship_city} onChange={(e) => set("ship_city", e.target.value)} />
        <input className={inp} placeholder="Ship postal code" value={f.ship_postal_code} onChange={(e) => set("ship_postal_code", e.target.value)} />
        <input className={inp} placeholder="Ship country (ISO)" maxLength={2} value={f.ship_country} onChange={(e) => set("ship_country", e.target.value.toUpperCase())} />
      </div>
      <div className="flex justify-end">
        <button className="btn-primary" disabled={!f.name || save.isPending} onClick={() => save.mutate()}>Save customer</button>
      </div>
    </div>
  );
}

function L({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block text-sm">
      <span className="mb-1 block text-xs font-medium text-slate-500">{label}</span>
      {children}
    </label>
  );
}
