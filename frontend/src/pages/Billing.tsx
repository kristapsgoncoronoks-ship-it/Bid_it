import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthContext";
import { api, apiError } from "../lib/api";
import type { BillingInfo } from "../lib/types";
import { isAdminOrAbove } from "../lib/roles";

export default function Billing() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const isOwner = isAdminOrAbove(user);

  const billing = useQuery<BillingInfo>({ queryKey: ["billing"], queryFn: async () => (await api.get("/billing")).data });

  const change = useMutation({
    mutationFn: async (plan: string) => (await api.put("/billing/plan", { plan })).data,
    onSuccess: (data) => {
      qc.setQueryData(["billing"], data);
      qc.invalidateQueries({ queryKey: ["modules"] });
    },
  });

  // When Stripe is connected, a paid plan starts a hosted Checkout session and
  // the "manage" button opens the Customer Portal; both redirect to Stripe.
  const checkout = useMutation({
    mutationFn: async (plan: string) => (await api.post("/billing/checkout", { plan })).data as { url: string },
    onSuccess: (data) => {
      window.location.href = data.url;
    },
  });
  const portal = useMutation({
    mutationFn: async () => (await api.post("/billing/portal", {})).data as { url: string },
    onSuccess: (data) => {
      window.location.href = data.url;
    },
  });

  const b = billing.data;
  const seatPct = b ? Math.min(100, Math.round((b.seats_used / b.seats_limit) * 100)) : 0;
  const stripeOn = !!b?.billing_enabled;
  const busy = change.isPending || checkout.isPending || portal.isPending;

  function choosePlan(planKey: string, priceEur: number | null) {
    // Paid plan + Stripe connected → Checkout. Otherwise the in-app switch.
    if (stripeOn && priceEur) checkout.mutate(planKey);
    else change.mutate(planKey);
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Plan &amp; billing</h1>
          <p className="text-sm text-slate-500">
            {stripeOn
              ? "Secure payments handled by Stripe. Paid plans start a checkout session."
              : "Prices are indicative — nothing is charged until billing is connected."}
          </p>
        </div>
        {stripeOn && b?.has_subscription && isOwner && (
          <button className="btn-ghost" disabled={busy} onClick={() => portal.mutate()}>
            Manage billing
          </button>
        )}
      </div>

      {(change.isError || checkout.isError || portal.isError) && (
        <div className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-600">
          {apiError(change.error || checkout.error || portal.error)}
        </div>
      )}

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
                disabled={current || !isOwner || busy}
                onClick={() => choosePlan(p.key, p.price_eur)}
              >
                {current
                  ? "Current plan"
                  : stripeOn && p.price_eur
                    ? `Subscribe to ${p.name}`
                    : `Switch to ${p.name}`}
              </button>
            </div>
          );
        })}
      </div>
      {!isOwner && <p className="text-xs text-slate-400">Only the workspace owner can change the plan.</p>}
    </div>
  );
}
