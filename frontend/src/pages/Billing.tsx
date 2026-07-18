import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthContext";
import { api, apiError } from "../lib/api";
import type { BillingInfo } from "../lib/types";

export default function Billing() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const isOwner = user?.role === "owner";

  const billing = useQuery<BillingInfo>({ queryKey: ["billing"], queryFn: async () => (await api.get("/billing")).data });

  const change = useMutation({
    mutationFn: async (plan: string) => (await api.put("/billing/plan", { plan })).data,
    onSuccess: (data) => {
      qc.setQueryData(["billing"], data);
      qc.invalidateQueries({ queryKey: ["modules"] });
    },
  });

  const b = billing.data;
  const seatPct = b ? Math.min(100, Math.round((b.seats_used / b.seats_limit) * 100)) : 0;

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Plan &amp; billing</h1>
        <p className="text-sm text-slate-500">Prices are indicative — nothing is charged until billing is connected.</p>
      </div>

      {change.isError && <div className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-600">{apiError(change.error)}</div>}

      {b && (
        <div className="card">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-sm text-slate-500">Current plan</div>
              <div className="text-xl font-semibold text-brand-700">{b.plan.name}</div>
            </div>
            <div className="text-right">
              <div className="text-sm text-slate-500">Seats</div>
              <div className="font-semibold">{b.seats_used} / {b.seats_limit}</div>
            </div>
          </div>
          <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-slate-200">
            <div className="h-full rounded-full bg-brand-500" style={{ width: `${seatPct}%` }} />
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-4">
        {b?.available_plans.map((p) => {
          const current = p.key === b.plan.key;
          return (
            <div key={p.key} className={`card flex flex-col ${current ? "ring-2 ring-brand-500" : ""}`}>
              <div className="text-sm font-semibold text-slate-700">{p.name}</div>
              <div className="mt-1 text-2xl font-bold">
                {p.price_eur === null ? "Custom" : p.price_eur === 0 ? "Free" : `€${p.price_eur}`}
                {p.price_eur ? <span className="text-sm font-normal text-slate-400">/mo</span> : null}
              </div>
              <ul className="mt-3 flex-1 space-y-1 text-sm text-slate-600">
                <li>• {p.seats} seats</li>
                <li>• Core analytics, intake, FX, validation</li>
                <li>• {p.modules.includes("issuing") ? "Invoice issuing included" : "No invoice issuing"}</li>
                {p.trial && <li className="text-amber-600">• Trial</li>}
              </ul>
              <button
                className={`mt-4 ${current ? "btn-ghost" : "btn-primary"}`}
                disabled={current || !isOwner || change.isPending}
                onClick={() => change.mutate(p.key)}
              >
                {current ? "Current plan" : `Switch to ${p.name}`}
              </button>
            </div>
          );
        })}
      </div>
      {!isOwner && <p className="text-xs text-slate-400">Only the workspace owner can change the plan.</p>}
    </div>
  );
}
