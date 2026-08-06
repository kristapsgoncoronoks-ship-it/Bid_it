import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { EntityPicker, useEntities } from "../components/EntityPicker";
import { ModuleInactive } from "../components/ModuleGate";
import { RefusalNotice } from "../components/RefusalNotice";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  PageHeader,
  QueryState,
  Skeleton,
  TextInput,
} from "../components/ui";
import { api, apiError, apiErrorCode } from "../lib/api";
import { hasVatPerm } from "../lib/roles";
import { countryActions, lifecycleActions, type LadderAction } from "../lib/transportAdmin";
import { useModules } from "../lib/useModules";
import type { VatLifecycle } from "../lib/types";

/**
 * Customer activation for VAT refunds (WO-80) — the SPA surface for WO-77's
 * `api/routes/transport/customers.py`, whose service module (`customer_lifecycle.py`)
 * recorded the gap in its own docstring: every transition is meant to be an
 * explicit admin click, and until WO-77 there was no route to click, let alone
 * a screen.
 *
 * WHY THIS SCREEN MATTERS MORE THAN IT LOOKS: the R44 gate inside
 * `lock.submit_claim` refuses a filing unless the customer is `active` AND the
 * refunding country is `active`. Two of the claim workspace's refusal sentences
 * (`customer_not_active`, `country_not_activated`) send an operator here, so a
 * state shown ambiguously is a claim that quietly misses the 30 September
 * deadline. Hence: the state is always named in words, and the ABSENCE of a
 * lifecycle row — which the gate treats exactly like not-active — is rendered
 * as the real state it is, never as an error and never as blank.
 *
 * The ladders are the service's own, mirrored in `lib/transportAdmin.ts`:
 * prospect → pending → active → inactive, and per country (none) → requested →
 * active. Only the transitions legal from the current state are offered; an
 * illegal one is still refused by the server and its refusal renders here
 * (master-context §6 — the mirror is cosmetic, the server is the control).
 * `inactive` is terminal in this slice because no re-onboarding edge exists in
 * the service, and the page says so rather than inventing a button (§10).
 */

const STATE_COPY: Record<string, { label: string; tone: "neutral" | "warning" | "success" | "danger"; meaning: string }> = {
  prospect: {
    label: "Prospect",
    tone: "neutral",
    meaning: "A pre-sale lead. Every claim gate refuses it, exactly as if it were not onboarded.",
  },
  pending: {
    label: "Onboarding",
    tone: "warning",
    meaning: "Being onboarded. Claims still can’t be filed until someone activates it.",
  },
  active: {
    label: "Active",
    tone: "success",
    meaning: "Open to refund claims — for the countries that are themselves active.",
  },
  inactive: {
    label: "Inactive",
    tone: "danger",
    meaning:
      "Churned. Past claims are untouched, and the next filing is refused. There is no route back in this version.",
  },
};

const NEVER_ONBOARDED = {
  label: "Never onboarded",
  tone: "neutral" as const,
  meaning:
    "This entity has no lifecycle record at all. The claim gate treats that exactly like not-active.",
};

export default function VatCustomersPage() {
  const qc = useQueryClient();
  const { user } = useAuth();
  const modules = useModules();
  const canWrite = hasVatPerm(user, "vat.write"); // cosmetic — the server enforces
  const [entityId, setEntityId] = useState("");
  const [country, setCountry] = useState("");
  const [refusal, setRefusal] = useState<{ code: string | null; detail: string } | null>(null);

  const enabled = modules.isEnabled("transport");
  const entities = useEntities(enabled);

  const lifecycle = useQuery<VatLifecycle>({
    queryKey: ["transport", "lifecycle", entityId],
    queryFn: async () => (await api.get(`/transport/customers/${entityId}/lifecycle`)).data,
    enabled: enabled && entityId !== "",
  });

  const onRefusal = (e: unknown) => setRefusal({ code: apiErrorCode(e), detail: apiError(e) });

  const transition = useMutation({
    mutationFn: async (v: { path: string; body?: { active: boolean } }) =>
      (await api.post(`/transport/customers/${entityId}/${v.path}`, v.body ?? {})).data,
    onSuccess: () => {
      setRefusal(null);
      qc.invalidateQueries({ queryKey: ["transport", "lifecycle"] });
    },
    onError: onRefusal,
  });

  const countryTransition = useMutation({
    mutationFn: async (v: { country: string; path: string; body?: { active: boolean } }) =>
      (
        await api.post(
          `/transport/customers/${entityId}/countries/${encodeURIComponent(v.country)}/${v.path}`,
          v.body ?? {},
        )
      ).data,
    onSuccess: () => {
      setRefusal(null);
      setCountry("");
      qc.invalidateQueries({ queryKey: ["transport", "lifecycle"] });
    },
    onError: onRefusal,
  });

  if (modules.isLoading) return <Skeleton className="h-24 w-full" />;
  if (!enabled) {
    return (
      <div className="space-y-6">
        <PageHeader title="Customer activation" />
        <ModuleInactive name={"Transport & VAT refunds"} />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Customer activation"
        description="A refund claim can only be filed for an active customer, in a country that is itself active. This is where both are set."
        actions={
          <Link to="/vat-claims" className="btn-ghost">
            Back to claims
          </Link>
        }
      />

      {refusal && (
        <RefusalNotice
          code={refusal.code}
          detail={refusal.detail}
          onDismiss={() => setRefusal(null)}
        />
      )}

      <Card title="Choose a legal entity">
        <div className="max-w-sm">
          <EntityPicker entities={entities.data} value={entityId} onChange={setEntityId} required />
        </div>
      </Card>

      {entityId === "" ? (
        <Card padded={false}>
          <EmptyState
            title="Pick an entity to see where it stands"
            description="Each legal entity is onboarded and activated separately, and each refunding country is activated separately again."
          />
        </Card>
      ) : (
        <QueryState
          query={lifecycle}
          loading={<Skeleton className="h-32 w-full" />}
          errorTitle="Couldn’t load this customer’s status"
        >
          {(data) => {
            const state = data.status ? STATE_COPY[data.status] : undefined;
            const copy = state ?? NEVER_ONBOARDED;
            const actions = lifecycleActions(data.status);
            return (
              <div className="grid gap-6 lg:grid-cols-3">
                <Card title="Customer status" className="lg:col-span-1">
                  <Badge tone={copy.tone}>{copy.label}</Badge>
                  <p className="mt-2 text-sm text-slate-600">{copy.meaning}</p>
                  <ol className="mt-4 flex flex-wrap gap-2" aria-label="Customer lifecycle">
                    {["prospect", "pending", "active", "inactive"].map((step) => (
                      <li key={step}>
                        <span
                          aria-current={data.status === step ? "step" : undefined}
                          className={
                            data.status === step
                              ? "inline-flex items-center rounded-full bg-brand-500 px-2.5 py-0.5 text-xs font-semibold text-white"
                              : "inline-flex items-center rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-slate-400"
                          }
                        >
                          {STATE_COPY[step].label}
                        </span>
                      </li>
                    ))}
                  </ol>
                  {canWrite && (
                    <div className="mt-4 space-y-3">
                      {actions.length === 0 ? (
                        <p className="text-xs text-slate-400">
                          There is no next step from here. Re-onboarding an inactive customer isn’t
                          something this version can do.
                        </p>
                      ) : (
                        actions.map((a: LadderAction) => (
                          <div key={`${a.path}-${a.label}`}>
                            <Button
                              size="sm"
                              variant={a.path === "inactive" ? "danger" : "primary"}
                              loading={
                                transition.isPending && transition.variables?.path === a.path
                              }
                              onClick={() => transition.mutate({ path: a.path, body: a.body })}
                            >
                              {a.label}
                            </Button>
                            <p className="mt-1 text-xs text-slate-400">{a.hint}</p>
                          </div>
                        ))
                      )}
                    </div>
                  )}
                </Card>

                <Card title="Refunding countries" className="lg:col-span-2">
                  <p className="text-xs text-slate-500">
                    Each member state you reclaim from is requested first — that step is where the
                    registration and power of attorney are gathered — and activated once it is in
                    place. Only an active country passes the filing gate.
                  </p>

                  {data.countries.length === 0 ? (
                    <div className="mt-4">
                      <EmptyState
                        title="No countries requested yet"
                        description="Request the first country this customer reclaims VAT from."
                      />
                    </div>
                  ) : (
                    <ul className="mt-4 space-y-3">
                      {data.countries.map((row) => {
                        const countryLadder = countryActions(row.status);
                        return (
                          <li
                            key={row.id}
                            className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-3 first:border-0 first:pt-0"
                          >
                            <span className="flex items-center gap-3">
                              <span className="font-mono text-sm text-slate-700">{row.country}</span>
                              <Badge tone={row.status === "active" ? "success" : "warning"}>
                                {row.status === "active" ? "Active" : "Requested"}
                              </Badge>
                              <span className="text-xs text-slate-400">
                                {row.status === "active"
                                  ? "Claims for this country pass the activation gate."
                                  : "Registration is being gathered — claims are still refused."}
                              </span>
                            </span>
                            {canWrite && (
                              <span className="flex gap-2">
                                {countryLadder.map((a: LadderAction) => (
                                  <Button
                                    key={`${row.country}-${a.path}-${a.label}`}
                                    size="sm"
                                    variant="secondary"
                                    loading={
                                      countryTransition.isPending &&
                                      countryTransition.variables?.country === row.country
                                    }
                                    onClick={() =>
                                      countryTransition.mutate({
                                        country: row.country,
                                        path: a.path,
                                        body: a.body,
                                      })
                                    }
                                  >
                                    {a.label}
                                  </Button>
                                ))}
                              </span>
                            )}
                          </li>
                        );
                      })}
                    </ul>
                  )}

                  {canWrite && (
                    <div className="mt-5 flex flex-wrap items-end gap-3 border-t border-slate-100 pt-4">
                      <TextInput
                        label="Add a refunding country"
                        hint="ISO code, e.g. LV"
                        maxLength={2}
                        value={country}
                        onChange={(e) => setCountry(e.target.value)}
                        placeholder="LV"
                      />
                      <Button
                        variant="secondary"
                        loading={countryTransition.isPending}
                        disabled={country.trim().length !== 2}
                        onClick={() =>
                          countryTransition.mutate({
                            country: country.trim().toUpperCase(),
                            path: "request",
                          })
                        }
                      >
                        Request
                      </Button>
                    </div>
                  )}
                </Card>
              </div>
            );
          }}
        </QueryState>
      )}
    </div>
  );
}
