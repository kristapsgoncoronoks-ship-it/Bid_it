import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";
import { useState } from "react";
import { useAuth } from "../auth/AuthContext";
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
  StatCard,
  TabPanel,
  Tabs,
  TextInput,
  inputClass,
} from "../components/ui";
import { api, apiError, apiErrorCode, downloadFile } from "../lib/api";
import { decimalMoney } from "../lib/format";
import { hasVatPerm } from "../lib/roles";
import { isMappedRefusal } from "../lib/transportClaims";
import {
  ADVISORY_NOTE,
  EXCISE_TABS,
  NO_RATE_EXPLANATION,
  PACKET_NOTE,
  RATE_SOURCE_NOTE,
  rateSourceLabel,
  type ExciseTabKey,
} from "../lib/transportExcise";
import { isDecimalShape, isPeriodShape } from "../lib/transportRecovery";
import { isCountryShape } from "../lib/transportSavings";
import { useModules } from "../lib/useModules";
import type { ExciseRates, ExciseReport } from "../lib/types";

/**
 * The diesel-excise screen (WO-92) — two panels over the five WO-91 routes
 * (`GET /transport/excise`, `/excise/packet`, `/excise/rates`, and
 * `PUT`/`DELETE /excise/rates`), which until now were reachable only with
 * `curl`.
 *
 * WHAT THIS NUMBER IS, AND WHAT IT IS NOT
 * -----------------------------------------
 * Around seven EU states refund part of the excise a haulier pays on commercial
 * diesel. This product sums the validated diesel litres it already holds, per
 * legal entity and country of supply, and multiplies them by the rate held for
 * that country. It stops there. Whether a given haulier actually qualifies —
 * vehicle weight, carrier registration — is deliberately NOT modelled by this
 * product, and the service says so in one sentence that rides every response.
 *
 * That limitation is structural on the server: one constant, rendered by every
 * result shape and both sheets of the customs workbook, plus a required literal
 * `eligibility_asserted: false` so the denial survives a surface that truncates
 * prose. **This screen must not undo any of it**, and the preservation is
 * asserted rather than described (`e2e/excise.spec.ts`):
 *
 *   1. `eligibility`, `rate_caveat`, `legal_framing`, `filed_with` and
 *      `litre_basis` are rendered VERBATIM off the wire — on the page header and
 *      again inside the figures panel, because a panel scrolled away from the
 *      header is its own surface. There is one string for each; a re-wording in
 *      the SPA cannot soften it, and the spec proves none of them exists
 *      anywhere under `src/`.
 *   2. No identifier, string or comment in this module or in
 *      `lib/transportExcise.ts` borrows a word from the family of vocabulary the
 *      backend refuses to use for this figure — the words that would turn an
 *      indicative arithmetic result into a sum a customs authority is asserted
 *      to be holding for this haulier. A column heading is exactly where that
 *      false assertion would enter, so the spec scans the source with a
 *      seeded-violation self-test proving the scan can still fail.
 *   3. A country this product holds no rate for is presented as **no finding** —
 *      its litres and its line count, and no euro. Printing €0.00 would say
 *      "this state refunds you nothing", which is a different and false
 *      statement from "we hold no rate for this state".
 *   4. Every rate is shown beside its SOURCE, because the shipped default is an
 *      explicit placeholder in a reported band and not a statutory figure.
 *
 * THE UI COMPUTES NOTHING (§4.9/§4.10). Every euro, every rate and every litre
 * figure arrives as a decimal STRING, already multiplied and already quantized
 * by the service with one ROUND_HALF_UP, and is rendered exactly as received.
 * There is no arithmetic anywhere in this file — a UI-side `litres / 1000 ×
 * rate` would be a second, forkable source of truth for a figure that goes in
 * front of a customs authority.
 *
 * ADVISORY (§4.19). The analysis and the packet are read-only: opening this page
 * changes no figure, no status and no document, and nothing here gates anything.
 * Setting or clearing a country rate is the one mutation, it is ordinary
 * configuration, and the server audits it.
 */

/** The current accounting month, "YYYY-MM". A clock read, never a money path. */
function currentPeriod(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

/**
 * The limitation, rendered from the wire. Every string here is the service's
 * own — this component chooses only where they sit. `eligibilityAsserted` is
 * rendered rather than dropped: the boolean exists precisely so the denial
 * survives a surface that truncates prose, and a surface that ignored it would
 * be that surface.
 */
function CaveatBlock({
  eligibility,
  eligibilityAsserted,
  rateCaveat,
  framing,
  filedWith,
  litreBasis,
}: {
  eligibility: string;
  eligibilityAsserted: boolean;
  rateCaveat: string;
  framing: string;
  filedWith?: string;
  litreBasis?: string;
}) {
  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
      <p className="font-semibold">{framing}</p>
      <p className="mt-2">{eligibility}</p>
      {!eligibilityAsserted && (
        <p className="mt-2 text-xs font-medium text-amber-800">
          Eligibility asserted by this figure: none. Confirm it yourself before
          you file anything.
        </p>
      )}
      <p className="mt-2 text-amber-800">{rateCaveat}</p>
      {filedWith && (
        <p className="mt-2 text-xs text-amber-700">Filed with: {filedWith}</p>
      )}
      {litreBasis && (
        <p className="mt-1 text-xs text-amber-700">Basis: {litreBasis}</p>
      )}
    </div>
  );
}

/** A small labelled count. Counts are integers the service reports, never money
 * and never derived here. */
function Counter({
  label,
  value,
  hint,
}: {
  label: string;
  value: number;
  hint?: string;
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-4 py-3">
      <p className="text-xs font-medium text-slate-500">{label}</p>
      <p className="mt-1 text-lg font-semibold tabular-nums text-slate-800">
        {value}
      </p>
      {hint && <p className="mt-1 text-xs text-slate-400">{hint}</p>}
    </div>
  );
}

/** The rate's provenance as a chip. Driven by the wire's own boolean; the two
 * words are the customs workbook's own pair, so the sheet and the screen
 * describe the same rate identically. */
function RateSource({ isOverride }: { isOverride: boolean }) {
  return (
    <Badge tone={isOverride ? "success" : "neutral"}>
      {rateSourceLabel(isOverride)}
    </Badge>
  );
}

export default function ExcisePage() {
  const modules = useModules();
  const [tab, setTab] = useState<ExciseTabKey>("figures");
  const [period, setPeriod] = useState(currentPeriod);
  const [country, setCountry] = useState("");
  const enabled = modules.isEnabled("transport");
  const periodReady = isPeriodShape(period);
  const countryReady = isCountryShape(country);
  const scope = country.trim().toUpperCase();

  // The rate registry needs no period, so it is the one response that is always
  // available — which makes it the right source for the page-level caveats.
  const rates = useQuery<ExciseRates>({
    queryKey: ["transport", "excise", "rates"],
    queryFn: async () => (await api.get("/transport/excise/rates")).data,
    enabled,
    retry: false,
  });

  if (modules.isLoading) return <Skeleton className="h-24 w-full" />;
  if (!enabled) {
    return (
      <div className="space-y-6">
        <PageHeader title="Diesel excise" />
        <ModuleInactive name={"Transport & VAT refunds"} />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Diesel excise"
        description="Validated diesel litres per legal entity and country of supply, at the excise rate held for that country. A separate regime from the VAT refund, handled by a customs authority — and an indicative figure only, because this product does not model whether you qualify for it."
      />

      {rates.data && (
        <CaveatBlock
          eligibility={rates.data.eligibility}
          eligibilityAsserted={rates.data.eligibility_asserted}
          rateCaveat={rates.data.rate_caveat}
          framing={rates.data.legal_framing}
        />
      )}

      <p className="text-xs text-slate-400">{ADVISORY_NOTE}</p>

      <Card title="Scope">
        <div className="flex flex-wrap items-end gap-4">
          <TextInput
            label="Period"
            hint="The month the diesel was supplied, YYYY-MM"
            value={period}
            onChange={(e) => setPeriod(e.target.value)}
            maxLength={7}
            placeholder="2026-05"
            className="w-40"
          />
          <TextInput
            label="Country"
            hint="Two-letter country of supply — leave empty for all"
            value={country}
            onChange={(e) => setCountry(e.target.value)}
            maxLength={2}
            placeholder="All countries"
            className="w-40"
          />
          {!periodReady && (
            <p className="pb-2 text-xs text-slate-400">
              Type a month as YYYY-MM to run the analysis.
            </p>
          )}
          {!countryReady && (
            <p className="pb-2 text-xs text-slate-400">
              Use a two-letter country code, or leave it empty.
            </p>
          )}
        </div>
      </Card>

      <Tabs
        tabs={EXCISE_TABS}
        value={tab}
        onChange={(v) => setTab(v as ExciseTabKey)}
        label="Diesel excise sections"
        idBase="excise"
      />

      {tab === "figures" && (
        <TabPanel idBase="excise" value="figures">
          <FiguresPanel
            period={period}
            scope={scope}
            ready={periodReady && countryReady}
          />
        </TabPanel>
      )}
      {tab === "rates" && (
        <TabPanel idBase="excise" value="rates">
          <RatesPanel rates={rates} />
        </TabPanel>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 1. The month's figures, and the packet built from them
// ---------------------------------------------------------------------------

function FiguresPanel({
  period,
  scope,
  ready,
}: {
  period: string;
  scope: string;
  ready: boolean;
}) {
  const [packetRefusal, setPacketRefusal] = useState<{
    code: string | null;
    detail: string;
  } | null>(null);

  const q = useQuery<ExciseReport>({
    queryKey: ["transport", "excise", "report", period, scope],
    queryFn: async () =>
      (
        await api.get("/transport/excise", {
          params: scope ? { period, country: scope } : { period },
        })
      ).data,
    enabled: ready,
    retry: false,
  });

  const failureCode = q.isError ? apiErrorCode(q.error) : null;
  const refused = isMappedRefusal(failureCode);

  // The packet is a READ (the router-level transport.read), so it is offered to
  // anyone who can see the figures. `downloadFile` re-inflates a blob error body
  // into the {detail, code} shape, which is what lets a refusal render a
  // sentence instead of a silent no-op. A refused download leaves the figures on
  // screen untouched — it is a second request, not a re-run of the analysis.
  const download = async () => {
    setPacketRefusal(null);
    const query = new URLSearchParams(
      scope ? { period, country: scope } : { period },
    );
    try {
      await downloadFile(
        `/transport/excise/packet?${query.toString()}`,
        `excise-${period}-customs-packet.xlsx`,
      );
    } catch (e) {
      setPacketRefusal({ code: apiErrorCode(e), detail: apiError(e) });
    }
  };

  return (
    <div className="space-y-6">
      {refused && (
        <RefusalNotice
          code={failureCode}
          detail={apiError(q.error)}
          periodShape="month"
        />
      )}

      {!refused && (
        <QueryState
          query={q}
          loading={<Skeleton className="h-40 w-full" />}
          errorTitle="Couldn’t run the diesel-excise analysis"
        >
          {(d) => (
            <div className="space-y-6">
              <CaveatBlock
                eligibility={d.eligibility}
                eligibilityAsserted={d.eligibility_asserted}
                rateCaveat={d.rate_caveat}
                framing={d.legal_framing}
                filedWith={d.filed_with}
                litreBasis={d.litre_basis}
              />

              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <StatCard
                  label="Indicative excise for the month"
                  value={decimalMoney(d.indicative_excise_eur, d.currency)}
                  sub={`${d.product_group} only, ${d.period}`}
                  accent="amber"
                />
                <div className="rounded-lg border border-slate-200 bg-white px-4 py-3">
                  <p className="text-xs font-medium text-slate-500">
                    Litres in the figure
                  </p>
                  <p className="mt-1 text-lg font-semibold tabular-nums text-slate-800">
                    {d.litres}
                  </p>
                  <p className="mt-1 text-xs text-slate-400">
                    Only the countries a rate is held for
                  </p>
                </div>
                <Counter
                  label="Lines examined"
                  value={d.lines_examined}
                  hint={`Validated ${d.product_group.toLowerCase()} lines in scope`}
                />
                <Counter
                  label="Countries with no rate held"
                  value={d.skipped_countries.length}
                  hint="Listed below with their litres — no figure, not a zero"
                />
              </div>

              <Card
                title="Per legal entity and country of supply"
                padded={false}
                actions={
                  <Button size="sm" variant="secondary" onClick={download}>
                    Download the customs packet
                  </Button>
                }
              >
                {packetRefusal && (
                  <div className="p-5 pb-0">
                    <RefusalNotice
                      code={packetRefusal.code}
                      detail={packetRefusal.detail}
                      periodShape="month"
                      onDismiss={() => setPacketRefusal(null)}
                    />
                  </div>
                )}
                {d.rows.length === 0 ? (
                  <EmptyState
                    title="No figure for this month"
                    description="A row needs both validated diesel litres in a country and a rate held for that country. The counters above say which of the two is missing, and the countries listed below had litres but no rate."
                  />
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <caption className="sr-only">
                        Diesel litres per legal entity and country of supply, at
                        the rate held for each country
                      </caption>
                      <thead>
                        <tr className="text-left text-xs text-slate-400">
                          <th className="px-5 py-2">Legal entity</th>
                          <th className="px-5 py-2">Country of supply</th>
                          <th className="px-5 py-2 text-right">Litres</th>
                          <th className="px-5 py-2 text-right">Lines</th>
                          <th className="px-5 py-2 text-right">
                            Rate €/1,000 L
                          </th>
                          <th className="px-5 py-2">Rate source</th>
                          <th className="px-5 py-2 text-right">
                            Indicative excise ({d.currency})
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {d.rows.map((r) => (
                          <tr
                            key={`${r.entity_id}-${r.country}`}
                            className="border-t border-slate-100"
                          >
                            <td className="px-5 py-2 font-medium text-slate-700">
                              {r.entity_name}
                              <span className="ml-2 text-xs text-slate-400">
                                {r.entity_id}
                              </span>
                            </td>
                            <td className="px-5 py-2 text-slate-600">
                              {r.country}
                            </td>
                            <td className="px-5 py-2 text-right tabular-nums">
                              {r.litres}
                            </td>
                            <td className="px-5 py-2 text-right tabular-nums text-slate-500">
                              {r.lines}
                            </td>
                            <td className="px-5 py-2 text-right tabular-nums">
                              {r.rate_eur_per_1000l}
                            </td>
                            <td className="px-5 py-2">
                              <RateSource isOverride={r.rate_is_override} />
                            </td>
                            <td className="px-5 py-2 text-right font-medium tabular-nums">
                              {decimalMoney(
                                r.indicative_excise_eur,
                                d.currency,
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>

              <p className="text-xs text-slate-400">{PACKET_NOTE}</p>

              <Card title="Countries with no rate held" padded={false}>
                {d.skipped_countries.length === 0 ? (
                  <div className="p-5 text-sm text-slate-500">
                    Every country with diesel litres this month has a rate held
                    for it, so nothing was left out of the figure above.
                  </div>
                ) : (
                  <>
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <caption className="sr-only">
                          Countries with validated diesel litres in the month for
                          which this product holds no rate
                        </caption>
                        <thead>
                          <tr className="text-left text-xs text-slate-400">
                            <th className="px-5 py-2">Country of supply</th>
                            <th className="px-5 py-2 text-right">Litres</th>
                            <th className="px-5 py-2 text-right">Lines</th>
                          </tr>
                        </thead>
                        <tbody>
                          {d.skipped_countries.map((s) => (
                            <tr
                              key={s.country}
                              className="border-t border-slate-100"
                            >
                              <td className="px-5 py-2 font-medium text-slate-700">
                                {s.country}
                              </td>
                              <td className="px-5 py-2 text-right tabular-nums">
                                {s.litres}
                              </td>
                              <td className="px-5 py-2 text-right tabular-nums text-slate-500">
                                {s.lines}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <p className="px-5 py-4 text-xs text-slate-500">
                      {NO_RATE_EXPLANATION}
                    </p>
                  </>
                )}
              </Card>
            </div>
          )}
        </QueryState>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 2. The rate registry the figures are computed from
// ---------------------------------------------------------------------------

function RatesPanel({
  rates,
}: {
  rates: UseQueryResult<ExciseRates>;
}) {
  const { user } = useAuth();
  const qc = useQueryClient();
  // Cosmetic mirror of the permission the two write routes actually declare
  // (`VAT_WRITE`). The server is the control; this only avoids rendering a
  // button whose click would be refused.
  const canWrite = hasVatPerm(user, "vat.write");
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [refusal, setRefusal] = useState<{
    code: string | null;
    detail: string;
  } | null>(null);

  const invalidate = () => {
    setRefusal(null);
    qc.invalidateQueries({ queryKey: ["transport", "excise"] });
  };
  const onRefusal = (e: unknown) =>
    setRefusal({ code: apiErrorCode(e), detail: apiError(e) });

  const setRate = useMutation({
    mutationFn: async (vars: { country: string; rate: string }) =>
      (
        await api.put("/transport/excise/rates", {
          country: vars.country,
          // The typed rate crosses the wire as the STRING the operator typed.
          // It is never parsed here — the service quantizes and refuses.
          rate_eur_per_1000l: vars.rate,
        })
      ).data as ExciseRates,
    onSuccess: (_d, vars) => {
      setDrafts((prev) => {
        const next = { ...prev };
        delete next[vars.country];
        return next;
      });
      invalidate();
    },
    onError: onRefusal,
  });

  const clearRate = useMutation({
    mutationFn: async (country: string) =>
      (await api.delete("/transport/excise/rates", { params: { country } }))
        .data as ExciseRates,
    onSuccess: invalidate,
    onError: onRefusal,
  });

  return (
    <div className="space-y-6">
      {refusal && (
        <RefusalNotice
          code={refusal.code}
          detail={refusal.detail}
          periodShape="month"
          onDismiss={() => setRefusal(null)}
        />
      )}

      <QueryState
        query={rates}
        loading={<Skeleton className="h-40 w-full" />}
        errorTitle="Couldn’t load the country rates"
      >
        {(d) => (
          <div className="space-y-6">
            <CaveatBlock
              eligibility={d.eligibility}
              eligibilityAsserted={d.eligibility_asserted}
              rateCaveat={d.rate_caveat}
              framing={d.legal_framing}
            />

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="rounded-lg border border-slate-200 bg-white px-4 py-3">
                <p className="text-xs font-medium text-slate-500">
                  Indicative default
                </p>
                <p className="mt-1 text-lg font-semibold tabular-nums text-slate-800">
                  {d.default_rate_eur_per_1000l} {d.currency} / 1,000 L
                </p>
                <p className="mt-1 text-xs text-slate-400">
                  Applied to every state below that has no typed override
                </p>
              </div>
              <Counter
                label="States this product records"
                value={d.countries.length}
                hint="A state absent from this list has no rate at all — which is not a rate of zero"
              />
            </div>

            <p className="text-sm text-slate-600">{RATE_SOURCE_NOTE}</p>

            <Card title="Rate by country" padded={false}>
              {d.rates.length === 0 ? (
                <EmptyState
                  title="No state is listed"
                  description="This product records no state as operating a commercial-diesel excise refund, so there is no rate to hold."
                />
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <caption className="sr-only">
                      The rate each state's figure is computed at, and whether it
                      is a typed override or the shipped placeholder
                    </caption>
                    <thead>
                      <tr className="text-left text-xs text-slate-400">
                        <th className="px-5 py-2">Country</th>
                        <th className="px-5 py-2 text-right">Rate €/1,000 L</th>
                        <th className="px-5 py-2">Rate source</th>
                        {canWrite && (
                          <th className="px-5 py-2">Type the rate customs applies</th>
                        )}
                      </tr>
                    </thead>
                    <tbody>
                      {d.rates.map((r) => {
                        const draft = drafts[r.country] ?? "";
                        const rateReady = isDecimalShape(draft);
                        return (
                          <tr
                            key={r.country}
                            className="border-t border-slate-100"
                          >
                            <td className="px-5 py-2 font-medium text-slate-700">
                              {r.country}
                            </td>
                            <td className="px-5 py-2 text-right tabular-nums">
                              {r.rate_eur_per_1000l}
                            </td>
                            <td className="px-5 py-2">
                              <RateSource isOverride={r.is_override} />
                            </td>
                            {canWrite && (
                              <td className="px-5 py-2">
                                <div className="flex flex-wrap items-center gap-2">
                                  <input
                                    aria-label={`Rate for ${r.country}`}
                                    value={draft}
                                    onChange={(e) =>
                                      setDrafts({
                                        ...drafts,
                                        [r.country]: e.target.value,
                                      })
                                    }
                                    placeholder={r.rate_eur_per_1000l}
                                    className={inputClass(false, "w-28")}
                                  />
                                  <Button
                                    size="sm"
                                    disabled={!rateReady}
                                    loading={
                                      setRate.isPending &&
                                      setRate.variables?.country === r.country
                                    }
                                    onClick={() =>
                                      setRate.mutate({
                                        country: r.country,
                                        rate: draft.trim(),
                                      })
                                    }
                                  >
                                    Save
                                  </Button>
                                  {r.is_override && (
                                    <Button
                                      size="sm"
                                      variant="ghost"
                                      loading={
                                        clearRate.isPending &&
                                        clearRate.variables === r.country
                                      }
                                      onClick={() =>
                                        clearRate.mutate(r.country)
                                      }
                                    >
                                      Clear override
                                    </Button>
                                  )}
                                </div>
                              </td>
                            )}
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>

            {!canWrite && (
              <p className="text-xs text-slate-400">
                Rates are read-only for your role. An administrator can type the
                rate a customs authority actually applies.
              </p>
            )}
          </div>
        )}
      </QueryState>
    </div>
  );
}
