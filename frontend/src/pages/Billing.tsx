import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthContext";
import { api, apiError } from "../lib/api";
import type { BillingInfo, ModuleInfo, PlanInfo } from "../lib/types";
import { isAdminOrAbove } from "../lib/roles";
import { useModules } from "../lib/useModules";
import { ConfirmDialog } from "../components/ui";

// Which currently-enabled, non-core modules would this target plan turn off?
// Real data only (R18): the org's actual enabled modules (`GET /modules`)
// diffed against the target plan's actual module allowlist (`PlanInfo.modules`
// from `GET /billing`) — never a guess.
function affectedModules(target: PlanInfo, modules: ModuleInfo[]): ModuleInfo[] {
  return modules.filter((m) => !m.core && m.enabled && !target.modules.includes(m.key));
}

export default function Billing() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const isOwner = isAdminOrAbove(user);
  const modulesInfo = useModules();
  const [confirmPlan, setConfirmPlan] = useState<PlanInfo | null>(null);

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
  const billingOn = !!b?.billing_enabled;
  const provider = b?.billing_provider ?? "none";
  const hasPortal = provider === "stripe";        // EveryPay has no hosted portal
  const busy = change.isPending || checkout.isPending || portal.isPending;

  function commitPlanChange(p: PlanInfo) {
    // Paid plan + a provider connected → hosted checkout. Otherwise in-app switch.
    if (billingOn && p.price_eur) checkout.mutate(p.key);
    else change.mutate(p.key);
  }

  // R18: a plan change can silently disable an in-use add-on module
  // (`PUT /billing/plan` / the Stripe webhook both reconcile modules down to
  // the target plan's allowlist). Warn first, naming what would be lost, and
  // only commit on explicit confirm. A change that drops nothing currently
  // enabled proceeds exactly as before — no added friction.
  function choosePlan(p: PlanInfo) {
    if (affectedModules(p, modulesInfo.data ?? []).length > 0) {
      setConfirmPlan(p);
      return;
    }
    commitPlanChange(p);
  }

  const providerBlurb: Record<string, string> = {
    stripe: "Secure payments handled by Stripe. Paid plans start a checkout session.",
    everypay: "Secure card payments handled by EveryPay. Paid plans open a hosted payment page.",
    none: "Prices are indicative — nothing is charged until billing is connected.",
  };

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Plan &amp; billing</h1>
          <p className="text-sm text-slate-500">{providerBlurb[provider] ?? providerBlurb.none}</p>
        </div>
        {hasPortal && b?.has_subscription && isOwner && (
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
          const selfService = p.price_eur !== null; // custom-priced plans (e.g. Enterprise) are never self-service
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
                <li>
                  • Archived invoices kept {p.archive_retention_years}{" "}
                  {p.archive_retention_years === 1 ? "year" : "years"}
                </li>
                {p.trial && <li className="text-amber-600">• Trial</li>}
              </ul>
              {!current && !selfService ? (
                <button className="mt-4 btn-ghost" disabled title="Custom pricing — not available for self-service switch">
                  Contact sales
                </button>
              ) : !current && !p.purchasable ? (
                // WO-AD: priced, but the provider has no price configured for it
                // yet. Offering "Subscribe" here would start a checkout that can
                // only fail — the server says so, and the button says so.
                <button className="mt-4 btn-ghost" disabled title="This plan is not yet available for purchase">
                  Not yet available
                </button>
              ) : (
                <button
                  className={`mt-4 ${current ? "btn-ghost" : "btn-primary"}`}
                  disabled={current || !isOwner || busy || modulesInfo.isLoading}
                  onClick={() => choosePlan(p)}
                >
                  {current
                    ? "Current plan"
                    : billingOn && p.price_eur
                      ? `Subscribe to ${p.name}`
                      : `Switch to ${p.name}`}
                </button>
              )}
            </div>
          );
        })}
      </div>
      {!isOwner && <p className="text-xs text-slate-400">Only the workspace owner can change the plan.</p>}

      <ConfirmDialog
        open={confirmPlan !== null}
        onClose={() => setConfirmPlan(null)}
        onConfirm={() => {
          const p = confirmPlan!;
          setConfirmPlan(null);
          commitPlanChange(p);
        }}
        title={`Switch to ${confirmPlan?.name}?`}
        confirmLabel={confirmPlan ? `Switch to ${confirmPlan.name}` : "Confirm"}
        tone="danger"
        loading={busy}
      >
        {confirmPlan && (
          <>
            <p className="text-sm text-slate-600">
              {confirmPlan.name} does not include the following module
              {affectedModules(confirmPlan, modulesInfo.data ?? []).length > 1 ? "s" : ""}, currently in
              use — switching will disable {affectedModules(confirmPlan, modulesInfo.data ?? []).length > 1 ? "them" : "it"}:
            </p>
            <ul className="mt-2 list-inside list-disc text-sm text-slate-700">
              {affectedModules(confirmPlan, modulesInfo.data ?? []).map((m) => (
                <li key={m.key}>{m.name}</li>
              ))}
            </ul>
          </>
        )}
      </ConfirmDialog>
    </div>
  );
}
