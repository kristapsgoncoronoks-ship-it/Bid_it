import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { Badge, Button, Card, EmptyState, Modal, QueryState, Skeleton } from "../components/ui";
import { api, apiError } from "../lib/api";
import { isAdminOrAbove } from "../lib/roles";
import type { TaxCode } from "../lib/types";

const CATEGORIES = ["standard", "reduced", "zero", "exempt", "reverse_charge"] as const;

const CATEGORY_TONE: Record<string, "success" | "warning" | "neutral"> = {
  standard: "success",
  reduced: "warning",
};

/** Tenant tax-code catalog: the VAT rates the issuing and capture surfaces
 * resolve against. Codes are archived, never deleted, so history keeps rating. */
export default function TaxCodesPage() {
  const qc = useQueryClient();
  const { user } = useAuth();
  const admin = isAdminOrAbove(user); // cosmetic — the server enforces SETTINGS_MANAGE
  const [showInactive, setShowInactive] = useState(false);
  const [creating, setCreating] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [form, setForm] = useState({ code: "", name: "", rate: "", category: "standard", country: "" });

  const codes = useQuery<TaxCode[]>({
    queryKey: ["tax-codes", showInactive],
    queryFn: async () =>
      (await api.get(`/tax-codes${showInactive ? "?include_inactive=true" : ""}`)).data,
  });

  const refresh = () => {
    setErr(null);
    qc.invalidateQueries({ queryKey: ["tax-codes"] });
  };

  const create = useMutation({
    mutationFn: async () =>
      (
        await api.post("/tax-codes", {
          code: form.code.trim(),
          name: form.name.trim(),
          rate: form.rate.trim(),
          category: form.category,
          country: form.country.trim() || null,
        })
      ).data as TaxCode,
    onSuccess: () => {
      setCreating(false);
      setForm({ code: "", name: "", rate: "", category: "standard", country: "" });
      refresh();
    },
    onError: (e) => setErr(apiError(e)),
  });

  const toggle = useMutation({
    mutationFn: async (tc: TaxCode) =>
      (await api.patch(`/tax-codes/${tc.id}`, { active: !tc.active, version: tc.version })).data,
    onSuccess: refresh,
    onError: (e) => {
      setErr(apiError(e));
      // A 409 means our version is stale — refetch so the next click carries the fresh one.
      qc.invalidateQueries({ queryKey: ["tax-codes"] });
    },
  });

  const input = "rounded-lg border border-slate-300 px-2 py-1 text-sm w-full";
  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-semibold">Tax codes</h1>
          <p className="text-slate-500">
            The VAT rates this workspace invoices and captures against. Archived codes still
            resolve for historical documents.
          </p>
        </div>
        {admin && <Button onClick={() => setCreating(true)}>New tax code</Button>}
      </div>

      {err && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-700">
          {err}
        </div>
      )}

      <label className="flex items-center gap-2 text-sm text-slate-500">
        <input
          type="checkbox"
          checked={showInactive}
          onChange={(e) => setShowInactive(e.target.checked)}
        />
        Show archived codes
      </label>

      <Card>
        <QueryState
          query={codes}
          loading={<Skeleton className="h-24 w-full" />}
          isEmpty={(rows) => rows.length === 0}
          empty={
            <EmptyState
              title="No tax codes yet"
              description={
                admin
                  ? "Create the VAT rates your invoices use — they feed every rate picker."
                  : "An administrator hasn't configured any tax codes yet."
              }
            />
          }
          errorTitle="Couldn’t load tax codes"
        >
          {(rows) => (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-slate-400">
                  <th className="py-1">Code</th>
                  <th className="py-1">Name</th>
                  <th className="py-1">Rate</th>
                  <th className="py-1">Category</th>
                  <th className="py-1">Country</th>
                  <th className="py-1">Status</th>
                  {admin && <th className="py-1"></th>}
                </tr>
              </thead>
              <tbody>
                {rows.map((tc) => (
                  <tr key={tc.id} className="border-t border-slate-100">
                    <td className="py-1 font-mono text-xs font-medium">{tc.code}</td>
                    <td className="py-1">{tc.name}</td>
                    <td className="py-1 tabular-nums">{tc.rate}%</td>
                    <td className="py-1">
                      <Badge tone={CATEGORY_TONE[tc.category] ?? "neutral"}>{tc.category}</Badge>
                    </td>
                    <td className="py-1">{tc.country ?? "—"}</td>
                    <td className="py-1">
                      {tc.active ? (
                        <Badge tone="success">active</Badge>
                      ) : (
                        <Badge tone="neutral">archived</Badge>
                      )}
                    </td>
                    {admin && (
                      <td className="py-1 text-right">
                        <Button
                          size="sm"
                          variant="secondary"
                          loading={toggle.isPending}
                          onClick={() => toggle.mutate(tc)}
                        >
                          {tc.active ? "Archive" : "Restore"}
                        </Button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </QueryState>
      </Card>

      <Modal open={creating} onClose={() => setCreating(false)} title="New tax code">
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <div className="grid grid-cols-2 gap-3">
            <label className="text-sm">
              Code
              <input
                className={input}
                required
                maxLength={24}
                value={form.code}
                onChange={(e) => setForm({ ...form, code: e.target.value })}
                placeholder="LV-STD"
              />
            </label>
            <label className="text-sm">
              Rate (%)
              <input
                className={input}
                required
                inputMode="decimal"
                value={form.rate}
                onChange={(e) => setForm({ ...form, rate: e.target.value })}
                placeholder="21"
              />
            </label>
          </div>
          <label className="block text-sm">
            Name
            <input
              className={input}
              required
              maxLength={120}
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="Latvia standard"
            />
          </label>
          <div className="grid grid-cols-2 gap-3">
            <label className="text-sm">
              Category
              <select
                className={input}
                value={form.category}
                onChange={(e) => setForm({ ...form, category: e.target.value })}
              >
                {CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-sm">
              Country (optional)
              <input
                className={input}
                maxLength={2}
                value={form.country}
                onChange={(e) => setForm({ ...form, country: e.target.value.toUpperCase() })}
                placeholder="LV"
              />
            </label>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="secondary" onClick={() => setCreating(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={create.isPending}>
              Create tax code
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  );
}
