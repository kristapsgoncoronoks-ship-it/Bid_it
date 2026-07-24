import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button, Card } from "../components/ui";
import { api, apiError } from "../lib/api";
import type { Vendor } from "../lib/types";

export default function VendorsPage() {
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<string, { iban: string; bic: string }>>({});

  const vendors = useQuery<Vendor[]>({
    queryKey: ["vendors"],
    queryFn: async () => (await api.get("/vendors")).data,
  });

  const save = useMutation({
    mutationFn: async ({ id, iban, bic }: { id: string; iban: string; bic: string }) =>
      (await api.patch(`/vendors/${id}`, { iban: iban || null, bic: bic || null })).data,
    onSuccess: () => {
      setErr(null);
      qc.invalidateQueries({ queryKey: ["vendors"] });
    },
    onError: (e) => setErr(apiError(e)),
  });

  const val = (v: Vendor, key: "iban" | "bic") =>
    draft[v.id]?.[key] ?? (v[key] ?? "");
  const set = (v: Vendor, key: "iban" | "bic", value: string) =>
    setDraft({
      ...draft,
      [v.id]: {
        iban: key === "iban" ? value : val(v, "iban"),
        bic: key === "bic" ? value : val(v, "bic"),
      },
    });

  const input = "rounded-lg border border-slate-300 px-2 py-1 text-sm";
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Suppliers</h1>
        <p className="text-slate-500">
          Bank details used when a payment run is exported as a SEPA credit-transfer file.
        </p>
      </div>

      {err && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-700">
          {err}
        </div>
      )}

      <Card>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-slate-400">
              <th className="py-1">Supplier</th>
              <th className="py-1">IBAN</th>
              <th className="py-1">BIC</th>
              <th className="py-1"></th>
            </tr>
          </thead>
          <tbody>
            {(vendors.data ?? []).map((v) => (
              <tr key={v.id} className="border-t border-slate-100">
                <td className="py-1 font-medium">{v.name}</td>
                <td className="py-1">
                  <input
                    className={`${input} w-56 font-mono`}
                    placeholder="—"
                    value={val(v, "iban")}
                    onChange={(e) => set(v, "iban", e.target.value)}
                  />
                </td>
                <td className="py-1">
                  <input
                    className={`${input} w-32`}
                    placeholder="—"
                    value={val(v, "bic")}
                    onChange={(e) => set(v, "bic", e.target.value)}
                  />
                </td>
                <td className="py-1 text-right">
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={!draft[v.id]}
                    loading={save.isPending}
                    onClick={() =>
                      save.mutate({ id: v.id, iban: val(v, "iban"), bic: val(v, "bic") })
                    }
                  >
                    Save
                  </Button>
                </td>
              </tr>
            ))}
            {(vendors.data ?? []).length === 0 && (
              <tr>
                <td colSpan={4} className="py-3 text-slate-400">
                  No suppliers yet — they appear once you receive their invoices.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
