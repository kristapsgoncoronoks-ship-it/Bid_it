import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthContext";
import { api, apiError } from "../lib/api";
import type { BillingInfo, ModuleInfo, PlanInfo } from "../lib/types";
import { useModules } from "../lib/useModules";
import { ConfirmDialog } from "../components/ui";

// Which currently-enabled, non-core modules would this target plan turn off?
// Real data only (R18): the org's actual enabled modules (`GET /modules`)
// diffed against the target plan's actual module allowlist (`PlanInfo.modules`
// from `GET /billing`) — never a guess.
function affectedModules(target: PlanInfo, modules: ModuleInfo[]): ModuleInfo[] {
  return modules.filter((m) => !m.core && m.enabled && !target.modules.includes(m.key));
}

// The plan key the owner chose when they left for the provider's Checkout —
// so the page they return to knows what "activated" means instead of guessing
// from whatever it happens to read first (R4 review U-1/Q-2).
const CHECKOUT_TARGET_KEY = "invoiceiq_checkout_target";
const ACTIVATION_WAIT_MS = 90_000;
const ACTIVATION_POLL_MS = 2_000;

function readCheckoutTarget(): string | null {
  try {
    return sessionStorage.getItem(CHECKOUT_TARGET_KEY);
  } catch {
    return null;
  }
}

function writeCheckoutTarget(plan: string | null): void {
  try {
    if (plan === null) sessionStorage.removeItem(CHECKOUT_TARGET_KEY);
    else sessionStorage.setItem(CHECKOUT_TARGET_KEY, plan);
  } catch {
    /* storage unavailable — the page falls back to "the plan changed" */
  }
}

type ActivationPhase = "idle" | "activating" | "timed_out";

export default function Billing() {
  const { hasPerm, org, refresh } = useAuth();
  const qc = useQueryClient();
  const navigate = useNavigate();
  // BILLING_MANAGE is the OWNER's permission (core/authz.py removes it from
  // ADMINISTRATOR explicitly). This read `isAdminOrAbove` — so an admin saw
  // enabled plan buttons the server was always going to refuse (PROD-003).
  const isOwner = hasPerm("billing.manage");
  const modulesInfo = useModules();
  const [confirmPlan, setConfirmPlan] = useState<PlanInfo | null>(null);

  // Reference R2/R4: after Checkout the plan is applied by the worker from the
  // durable Stripe webhook job, not on the request path — so the page the
  // provider returns to (`BILLING_SUCCESS_URL` = `/billing?checkout=success`)
  // may still show the old plan for a few seconds. While that is so we poll
  // until the plan the owner bought is the current plan (or 90 s pass), say
  // so, and keep the plan buttons disabled so nobody subscribes twice. When the
  // provider applied the plan before redirecting (EveryPay always, Stripe when
  // the webhook beats the browser) the first read already matches and nothing
  // is shown. Once settled the query string is dropped so a reload does not
  // start another wait.
  const arrivedFromCheckout = new URLSearchParams(window.location.search).get("checkout") === "success";
  const [target] = useState<string | null>(() => (arrivedFromCheckout ? readCheckoutTarget() : null));
  const [phase, setPhase] = useState<ActivationPhase>(arrivedFromCheckout ? "activating" : "idle");
  const [planAtArrival, setPlanAtArrival] = useState<string | null>(null);
  const activating = phase === "activating";

  const billing = useQuery<BillingInfo>({
    queryKey: ["billing"],
    queryFn: async () => (await api.get("/billing")).data,
    refetchInterval: activating ? ACTIVATION_POLL_MS : false,
  });

  useEffect(() => {
    if (!activating || !billing.data) return;
    const current = billing.data.plan.key;
    let settled: boolean;
    if (target !== null) {
      settled = current === target;
    } else {
      // No stored target (another tab or browser): settle when the plan
      // changes from what this page first read.
      if (planAtArrival === null) {
        setPlanAtArrival(current);
        return;
      }
      settled = current !== planAtArrival;
    }
    if (!settled) return;
    setPhase("idle");
    writeCheckoutTarget(null);
    qc.invalidateQueries({ queryKey: ["modules"] });
    // The served organization status (a suspended workspace reactivating) and
    // permissions can change with the plan — re-read the identity.
    void refresh().catch(() => undefined);
    navigate("/billing", { replace: true });
  }, [activating, billing.data, target, planAtArrival, qc, refresh, navigate]);

  useEffect(() => {
    if (!activating) return;
    const t = window.setTimeout(() => setPhase("timed_out"), ACTIVATION_WAIT_MS);
    return () => window.clearTimeout(t);
  }, [activating]);

  const change = useMutation({
    meta: { silent: true }, // rendered inline below (R2-B2)
    mutationFn: async (plan: string) => (await api.put("/billing/plan", { plan })).data,
    onSuccess: (data) => {
      qc.setQueryData(["billing"], data);
      qc.invalidateQueries({ queryKey: ["modules"] });
    },
  });

  // When Stripe is connected, a paid plan starts a hosted Checkout session and
  // the "manage" button opens the Customer Portal; both redirect to Stripe.
  const checkout = useMutation({
    meta: { silent: true }, // rendered inline below (R2-B2)
    mutationFn: async (plan: string) => (await api.post("/billing/checkout", { plan })).data as { url: string },
    onMutate: (plan) => {
      writeCheckoutTarget(plan);
    },
    onError: () => {
      writeCheckoutTarget(null);
    },
    onSuccess: (data) => {
      window.location.href = data.url;
    },
  });
  const portal = useMutation({
    meta: { silent: true }, // rendered inline below (R2-B2)
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
  // BE-022: a workspace that already holds a subscription changes plan (and
  // payment method) in the provider's Portal. A second Checkout would open a
  // SECOND subscription — the server refuses it (409) and the button never
  // offers it. The Portal opens for any paid plan; which switches it allows is
  // the Portal's configuration (DECISIONS §25).
  const changesViaPortal = billingOn && hasPortal && !!b?.has_subscription;
  const busy = change.isPending || checkout.isPending || portal.isPending || activating;
  // After the wait ran out the page is usable again, except for the plan that
  // was bought: subscribing to it a second time is the one thing that must
  // not happen while the provider may still be about to apply the first one.
  const boughtButNotApplied = phase === "timed_out" && target !== null;

  function commitPlanChange(p: PlanInfo) {
    // Paid plan + a provider connected → hosted checkout, or the Portal when a
    // subscription already exists — and for a subscriber the FREE plan too:
    // an in-app switch cannot cancel the subscription (BE-023), so the
    // Portal's cancel is the only honest one. Otherwise in-app switch.
    if (changesViaPortal) portal.mutate();
    else if (billingOn && p.price_eur) checkout.mutate(p.key);
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
    stripe: changesViaPortal
      ? "Your workspace already has a subscription — plan, payment-method and cancellation changes happen in Manage billing (Stripe), so a second subscription is never started."
      : "Secure payments handled by Stripe. Paid plans start a checkout session.",
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

      {b && b.status !== "active" && (
        <div role="alert" className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
          <div className="font-semibold">This workspace is {b.status}.</div>
          <p className="mt-1">
            {b.status === "suspended"
              ? billingOn
                ? changesViaPortal
                  ? "The last subscription payment did not go through. Update the payment method or the plan through Manage billing — access to the rest of the workspace returns as soon as a payment settles."
                  : "The last subscription payment did not go through, or the plan lapsed. Choose a plan below or update the payment method — access to the rest of the workspace returns as soon as a payment settles."
                : "The workspace was suspended by the platform. Contact support to restore it; the plan below can be reviewed but no payment is collected here yet."
              : "Contact support to reopen it."}
          </p>
        </div>
      )}

      {activating && (
        <div role="status" aria-live="polite" className="rounded-lg border border-brand-200 bg-brand-50 px-4 py-3 text-sm text-brand-800">
          Checkout complete — activating your plan… This usually takes a few seconds; the plan buttons are
          disabled meanwhile so nothing is subscribed twice.
        </div>
      )}
      {phase === "timed_out" && (
        <div role="alert" className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <div className="font-semibold">Checkout completed, but the plan has not updated yet.</div>
          <p className="mt-1">
            Do not subscribe again. Refresh this page in a minute; if it still shows the old plan, contact
            support and quote workspace {org?.id ?? "id unavailable"}.
          </p>
        </div>
      )}
      {(change.isError || checkout.isError || portal.isError) && (
        <div role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-600">
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
                  disabled={
                    current ||
                    !isOwner ||
                    busy ||
                    modulesInfo.isLoading ||
                    // After the wait ran out EVERY paid plan stays off: a
                    // second Checkout for any plan is the second subscription
                    // (R6 review A1); the server refuses it too while the
                    // first is in flight.
                    (boughtButNotApplied && !!p.price_eur)
                  }
                  onClick={() => choosePlan(p)}
                >
                  {current
                    ? "Current plan"
                    : changesViaPortal
                      ? p.price_eur
                        ? `Change to ${p.name} via Manage billing`
                        : "Cancel subscription via Manage billing"
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
        title={
          changesViaPortal
            ? `Change to ${confirmPlan?.name} in Manage billing?`
            : billingOn && confirmPlan?.price_eur
              ? `Subscribe to ${confirmPlan?.name}?`
              : `Switch to ${confirmPlan?.name}?`
        }
        confirmLabel={
          !confirmPlan
            ? "Confirm"
            : changesViaPortal
              ? "Continue to Manage billing"
              : billingOn && confirmPlan.price_eur
                ? `Subscribe to ${confirmPlan.name}`
                : `Switch to ${confirmPlan.name}`
        }
        tone="danger"
        loading={busy}
      >
        {confirmPlan && (
          <>
            <p className="text-sm text-slate-600">
              {confirmPlan.name} does not include the following module
              {affectedModules(confirmPlan, modulesInfo.data ?? []).length > 1 ? "s" : ""}, currently in
              use —{" "}
              {changesViaPortal
                ? `if you complete the change there, ${affectedModules(confirmPlan, modulesInfo.data ?? []).length > 1 ? "they" : "it"} will be disabled when the new plan is applied`
                : `switching will disable ${affectedModules(confirmPlan, modulesInfo.data ?? []).length > 1 ? "them" : "it"}`}
              :
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
