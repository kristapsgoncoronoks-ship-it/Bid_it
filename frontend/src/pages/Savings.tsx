import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { ModuleInactive } from "../components/ModuleGate";
import { RefusalNotice } from "../components/RefusalNotice";
import {
  Card,
  EmptyState,
  PageHeader,
  QueryState,
  Skeleton,
  StatCard,
  TabPanel,
  Tabs,
  TextInput,
} from "../components/ui";
import { api, apiError, apiErrorCode } from "../lib/api";
import { decimalMoney } from "../lib/format";
import { isMappedRefusal } from "../lib/transportClaims";
import { isPeriodShape } from "../lib/transportRecovery";
import {
  ADVISORY_NOTE,
  EXPECTED_REBATE_NOTE,
  NON_RECONCILIATION,
  NO_RIVAL_EXPLANATION,
  NO_SUPPLIER_FILTER_REASON,
  SAVINGS_TABS,
  isCountryShape,
  isSupplierShape,
  type SavingsTabKey,
} from "../lib/transportSavings";
import { useModules } from "../lib/useModules";
import type {
  ExpectedRebate,
  InternalBenchmark,
  SameDayOverpay,
} from "../lib/types";

/**
 * The negotiation-evidence workspace (WO-90) — three panels over the three
 * WO-87 read routes (`/transport/savings/same-day`, `/internal-benchmark`,
 * `/expected-rebate`), which until now were reachable only with `curl`.
 *
 * WHAT THESE NUMBERS ARE, AND WHAT THEY ARE NOT (R53's second framing)
 * ----------------------------------------------------------------------
 * The backend keeps two analysis families structurally apart. One measures a
 * breach of an agreed commercial term and produces a formal letter with a
 * payment deadline. This screen is the OTHER one: nobody ever agreed that a
 * supplier would match the cheapest rival network on the day, so there is no
 * term, no breach, and no obligation on anybody. These figures are evidence to
 * take into a negotiation, and the service states that on every response in one
 * string, which this page renders VERBATIM rather than restating.
 *
 * The separation is structural here too, not editorial, and it is asserted
 * rather than described (`e2e/savings.spec.ts`):
 *
 *   1. `legal_framing` and `price_basis` are rendered off the wire on all three
 *      panels. There is one string; a re-wording in the SPA cannot soften it.
 *   2. No identifier, string or comment in this module or in
 *      `lib/transportSavings.ts` uses the vocabulary the backend refuses to use
 *      for these figures. A column heading is exactly where a false assertion
 *      would enter, so the spec scans the source with a seeded-violation
 *      self-test proving the scan can still fail.
 *   3. There is no link and no route from this page into the contract-breach
 *      flow, so no figure computed here can travel into a frozen letter figure.
 *   4. There is no mutating control of any kind — nothing to send, nothing to
 *      open, nothing to freeze. The three routes are GET-only and so is this.
 *
 * R52 — TWO GRAINS THAT DO NOT RECONCILE, SAID OUT LOUD
 * -------------------------------------------------------
 * The same fuel lines produce two different totals: the same-day grain compares
 * each supplier against the cheapest RIVAL that traded that day, the monthly
 * grain compares it against the best price this organization itself paid that
 * month. Each response carries its own `grain` label and the two euro fields are
 * named differently on the wire so nothing can add them. This page renders both
 * grain labels and states the non-reconciliation in words — the first operator
 * to open both panels is told why they differ instead of assuming one is broken.
 *
 * THE UI COMPUTES NOTHING (§4.9/§4.10). Every euro, every €/L and every litre
 * figure arrives as a decimal STRING, already totalled and already quantized by
 * the service, and is rendered exactly as received. There is no addition, no
 * difference and no rounding anywhere in this file.
 *
 * ADVISORY (§4.19). All three analyses are read-only: opening this page changes
 * no figure, no status and no document, and nothing here gates anything.
 */

/** The current accounting month, "YYYY-MM". A clock read, never a money path. */
function currentPeriod(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

/** The basis + framing strip every panel renders from its OWN response. Both
 * strings are the service's; this component chooses only where they sit. */
function BasisAndFraming({
  priceBasis,
  framing,
}: {
  priceBasis: string;
  framing: string;
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm">
      <p className="font-medium text-slate-700">{priceBasis}</p>
      <p className="mt-1 text-slate-600">{framing}</p>
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

export default function SavingsPage() {
  const modules = useModules();
  const [tab, setTab] = useState<SavingsTabKey>("same-day");
  const [period, setPeriod] = useState(currentPeriod);
  const enabled = modules.isEnabled("transport");
  const periodReady = isPeriodShape(period);

  if (modules.isLoading) return <Skeleton className="h-24 w-full" />;
  if (!enabled) {
    return (
      <div className="space-y-6">
        <PageHeader title="Negotiation evidence" />
        <ModuleInactive name={"Transport & VAT refunds"} />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Negotiation evidence"
        description="What your fuel spend would have cost at the better prices already available to you — per day against the cheapest rival network, per month against your own best supplier, and the rebate a line quietly lost. Net EUR per litre, exactly as the service computed it."
      />

      <div className="rounded-lg border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-900">
        <p className="font-semibold">
          Evidence for a negotiation — not an obligation on anyone
        </p>
        <p className="mt-1 text-sky-800">
          Nobody agreed that a supplier would match the cheapest rival network
          on the day, so nothing on this page is money anybody is contractually
          obliged to pay you. These figures exist to be put on the table at a
          supplier meeting. Each panel shows the service's own wording of this
          beneath its price basis.
        </p>
        <p className="mt-2 text-xs text-sky-700">{ADVISORY_NOTE}</p>
      </div>

      <Card title="Accounting month">
        <div className="flex flex-wrap items-end gap-4">
          <TextInput
            label="Period"
            hint="The month the fuel was supplied, YYYY-MM"
            value={period}
            onChange={(e) => setPeriod(e.target.value)}
            maxLength={7}
            placeholder="2026-05"
            className="w-40"
          />
          {!periodReady && (
            <p className="pb-2 text-xs text-slate-400">
              Type a month as YYYY-MM to run the analyses.
            </p>
          )}
        </div>
      </Card>

      <Tabs
        tabs={SAVINGS_TABS}
        value={tab}
        onChange={(v) => setTab(v as SavingsTabKey)}
        label="Negotiation evidence sections"
        idBase="savings"
      />

      {tab === "same-day" && (
        <TabPanel idBase="savings" value="same-day">
          <SameDayPanel period={period} periodReady={periodReady} />
        </TabPanel>
      )}
      {tab === "benchmark" && (
        <TabPanel idBase="savings" value="benchmark">
          <BenchmarkPanel period={period} periodReady={periodReady} />
        </TabPanel>
      )}
      {tab === "rebate" && (
        <TabPanel idBase="savings" value="rebate">
          <RebatePanel period={period} periodReady={periodReady} />
        </TabPanel>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 1. Same-day overpay — R52 grain (a)
// ---------------------------------------------------------------------------

function SameDayPanel({
  period,
  periodReady,
}: {
  period: string;
  periodReady: boolean;
}) {
  const [country, setCountry] = useState("");
  const countryReady = isCountryShape(country);
  const scope = country.trim().toUpperCase();

  const q = useQuery<SameDayOverpay>({
    queryKey: ["transport", "savings", "same-day", period, scope],
    queryFn: async () =>
      (
        await api.get("/transport/savings/same-day", {
          params: scope ? { period, country: scope } : { period },
        })
      ).data,
    enabled: periodReady && countryReady,
    retry: false,
  });

  const failureCode = q.isError ? apiErrorCode(q.error) : null;
  const refused = isMappedRefusal(failureCode);

  return (
    <div className="space-y-6">
      <CountryFilter
        value={country}
        onChange={setCountry}
        valid={countryReady}
      />
      <p className="text-xs text-slate-400">{NO_SUPPLIER_FILTER_REASON}</p>

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
          errorTitle="Couldn’t run the same-day comparison"
        >
          {(d) => (
            <div className="space-y-6">
              <BasisAndFraming
                priceBasis={d.price_basis}
                framing={d.legal_framing}
              />

              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <StatCard
                  label="Avoidable at the cheaper rival"
                  value={decimalMoney(d.avoidable_eur, d.currency)}
                  sub={`${d.product_group} only, ${d.period}`}
                  accent="amber"
                />
                <Counter
                  label="Days compared"
                  value={d.days_compared}
                  hint="Days where at least two suppliers traded"
                />
                <Counter
                  label="Days with no rival"
                  value={d.days_without_a_rival}
                  hint="One supplier only — no comparison was possible"
                />
                <Counter label="Lines compared" value={d.lines_compared} />
              </div>

              <p className="text-xs text-slate-500">
                Grain:{" "}
                <span className="font-medium text-slate-700">{d.grain}</span>.{" "}
                {NON_RECONCILIATION}
              </p>

              <div className="rounded-lg border border-slate-200 bg-white px-4 py-3 text-sm text-slate-600">
                <p className="font-medium text-slate-700">
                  {d.days_without_a_rival === 1
                    ? "One day this month had no rival to compare against"
                    : `${d.days_without_a_rival} days this month had no rival to compare against`}
                </p>
                <p className="mt-1">{NO_RIVAL_EXPLANATION}</p>
                {d.lines_skipped_zero_qty > 0 && (
                  <p className="mt-2 text-xs text-slate-400">
                    {d.lines_skipped_zero_qty} line
                    {d.lines_skipped_zero_qty === 1 ? " was" : "s were"} skipped
                    for carrying no litres — a price per litre cannot be formed
                    from them.
                  </p>
                )}
              </div>

              <Card title="Where the difference arose" padded={false}>
                {d.findings.length === 0 ? (
                  <EmptyState
                    title="No day in this month priced above a rival"
                    description="Either every comparable day was already at the cheapest available price, or no day had two suppliers trading in the same country. The counters above say which."
                  />
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <caption className="sr-only">
                        Same-day, same-country comparison against the cheapest
                        rival network
                      </caption>
                      <thead>
                        <tr className="text-left text-xs text-slate-400">
                          <th className="px-5 py-2">Date</th>
                          <th className="px-5 py-2">Country</th>
                          <th className="px-5 py-2">Supplier</th>
                          <th className="px-5 py-2 text-right">Litres</th>
                          <th className="px-5 py-2 text-right">Paid €/L</th>
                          <th className="px-5 py-2">Cheapest rival</th>
                          <th className="px-5 py-2 text-right">Rival €/L</th>
                          <th className="px-5 py-2 text-right">
                            Difference €/L
                          </th>
                          <th className="px-5 py-2 text-right">
                            Avoidable ({d.currency})
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {d.findings.map((f) => (
                          <tr
                            key={`${f.country}-${f.txn_date}-${f.supplier}`}
                            className="border-t border-slate-100"
                          >
                            <td className="px-5 py-2 text-slate-600">
                              {f.txn_date}
                            </td>
                            <td className="px-5 py-2 text-slate-600">
                              {f.country}
                            </td>
                            <td className="px-5 py-2 font-medium text-slate-700">
                              {f.supplier}
                            </td>
                            <td className="px-5 py-2 text-right tabular-nums">
                              {f.litres}
                            </td>
                            <td className="px-5 py-2 text-right tabular-nums">
                              {f.eur_l_eff}
                            </td>
                            <td className="px-5 py-2 text-slate-600">
                              {f.cheapest_rival_supplier}
                              <span className="ml-2 text-xs text-slate-400">
                                {f.suppliers_that_day} suppliers that day
                              </span>
                            </td>
                            <td className="px-5 py-2 text-right tabular-nums">
                              {f.cheapest_rival_eur_l_eff}
                            </td>
                            <td className="px-5 py-2 text-right tabular-nums">
                              {f.delta_eur_l}
                            </td>
                            <td className="px-5 py-2 text-right font-medium tabular-nums">
                              {decimalMoney(f.avoidable_eur, d.currency)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>

              <Card title="By supplier and country">
                {d.by_supplier.length === 0 ? (
                  <p className="text-sm text-slate-500">
                    Nothing to attribute — no day in {d.period} priced above a
                    rival.
                  </p>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <caption className="sr-only">
                        Same-day difference attributed to the country of supply
                        and the supplier
                      </caption>
                      <thead>
                        <tr className="text-left text-xs text-slate-400">
                          <th className="py-2">Supplier</th>
                          <th className="py-2">Country</th>
                          <th className="py-2 text-right">Litres</th>
                          <th className="py-2 text-right">Days</th>
                          <th className="py-2 text-right">
                            Avoidable ({d.currency})
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {d.by_supplier.map((t) => (
                          <tr
                            key={`${t.country}-${t.supplier}`}
                            className="border-t border-slate-100"
                          >
                            <td className="py-2 font-medium text-slate-700">
                              {t.supplier}
                            </td>
                            <td className="py-2 text-slate-600">{t.country}</td>
                            <td className="py-2 text-right tabular-nums">
                              {t.litres}
                            </td>
                            <td className="py-2 text-right tabular-nums">
                              {t.days}
                            </td>
                            <td className="py-2 text-right font-medium tabular-nums">
                              {decimalMoney(t.avoidable_eur, d.currency)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
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
// 2. Internal benchmark — R52 grain (b)
// ---------------------------------------------------------------------------

function BenchmarkPanel({
  period,
  periodReady,
}: {
  period: string;
  periodReady: boolean;
}) {
  const [country, setCountry] = useState("");
  const countryReady = isCountryShape(country);
  const scope = country.trim().toUpperCase();

  const q = useQuery<InternalBenchmark>({
    queryKey: ["transport", "savings", "internal-benchmark", period, scope],
    queryFn: async () =>
      (
        await api.get("/transport/savings/internal-benchmark", {
          params: scope ? { period, country: scope } : { period },
        })
      ).data,
    enabled: periodReady && countryReady,
    retry: false,
  });

  const failureCode = q.isError ? apiErrorCode(q.error) : null;
  const refused = isMappedRefusal(failureCode);

  return (
    <div className="space-y-6">
      <CountryFilter
        value={country}
        onChange={setCountry}
        valid={countryReady}
      />

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
          errorTitle="Couldn’t run the internal benchmark"
        >
          {(d) => (
            <div className="space-y-6">
              <BasisAndFraming
                priceBasis={d.price_basis}
                framing={d.legal_framing}
              />

              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <StatCard
                  label="Gap to your own best supplier"
                  value={decimalMoney(d.benchmark_gap_eur, d.currency)}
                  sub={`${d.product_group} only, ${d.period}`}
                  accent="brand"
                />
                <Counter
                  label="Countries compared"
                  value={d.countries_compared}
                />
                <Counter
                  label="Suppliers compared"
                  value={d.suppliers_compared}
                />
                <Counter label="Lines compared" value={d.lines_compared} />
              </div>

              <p className="text-xs text-slate-500">
                Grain:{" "}
                <span className="font-medium text-slate-700">{d.grain}</span>.{" "}
                {NON_RECONCILIATION}
              </p>

              <p className="text-sm text-slate-600">
                Every price here is one you yourself paid, so this figure is
                what routing the same volume to the cheaper supplier you were
                already using would have cost instead. The best supplier in each
                country stays on the table with a zero gap — it is the supplier
                the volume would move to.
                {d.lines_skipped_zero_qty > 0 && (
                  <span className="ml-1 text-slate-400">
                    {d.lines_skipped_zero_qty} line
                    {d.lines_skipped_zero_qty === 1 ? " was" : "s were"} skipped
                    for carrying no litres.
                  </span>
                )}
              </p>

              <Card
                title="Supplier against the best of your own"
                padded={false}
              >
                {d.rows.length === 0 ? (
                  <EmptyState
                    title="No diesel volume to benchmark in this month"
                    description="A country needs at least one priced diesel line in the month before a best-supplier comparison can be made."
                  />
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <caption className="sr-only">
                        Country × month comparison against the best of your own
                        suppliers
                      </caption>
                      <thead>
                        <tr className="text-left text-xs text-slate-400">
                          <th className="px-5 py-2">Country</th>
                          <th className="px-5 py-2">Supplier</th>
                          <th className="px-5 py-2 text-right">Litres</th>
                          <th className="px-5 py-2 text-right">Paid €/L</th>
                          <th className="px-5 py-2">Best supplier</th>
                          <th className="px-5 py-2 text-right">Best €/L</th>
                          <th className="px-5 py-2 text-right">Gap €/L</th>
                          <th className="px-5 py-2 text-right">
                            Gap ({d.currency})
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {d.rows.map((r) => (
                          <tr
                            key={`${r.country}-${r.supplier}`}
                            className="border-t border-slate-100"
                          >
                            <td className="px-5 py-2 text-slate-600">
                              {r.country}
                            </td>
                            <td className="px-5 py-2 font-medium text-slate-700">
                              {r.supplier}
                            </td>
                            <td className="px-5 py-2 text-right tabular-nums">
                              {r.litres}
                            </td>
                            <td className="px-5 py-2 text-right tabular-nums">
                              {r.eur_l_eff}
                            </td>
                            <td className="px-5 py-2 text-slate-600">
                              {r.best_supplier}
                            </td>
                            <td className="px-5 py-2 text-right tabular-nums">
                              {r.best_eur_l_eff}
                            </td>
                            <td className="px-5 py-2 text-right tabular-nums">
                              {r.gap_eur_l}
                            </td>
                            <td className="px-5 py-2 text-right font-medium tabular-nums">
                              {decimalMoney(r.benchmark_gap_eur, d.currency)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
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
// 3. Expected rebate — the per-line half of §2.5's learning analysis
// ---------------------------------------------------------------------------

function RebatePanel({
  period,
  periodReady,
}: {
  period: string;
  periodReady: boolean;
}) {
  const [supplier, setSupplier] = useState("");
  const supplierReady = isSupplierShape(supplier);
  const scope = supplier.trim();

  const q = useQuery<ExpectedRebate>({
    queryKey: ["transport", "savings", "expected-rebate", period, scope],
    queryFn: async () =>
      (
        await api.get("/transport/savings/expected-rebate", {
          params: scope ? { period, supplier: scope } : { period },
        })
      ).data,
    enabled: periodReady && supplierReady,
    retry: false,
  });

  const failureCode = q.isError ? apiErrorCode(q.error) : null;
  const refused = isMappedRefusal(failureCode);

  return (
    <div className="space-y-6">
      <Card title="Scope">
        <div className="flex flex-wrap items-end gap-4">
          <TextInput
            label="Supplier"
            hint="Fuel-card or toll-network code — leave empty for all"
            value={supplier}
            onChange={(e) => setSupplier(e.target.value)}
            placeholder="All suppliers"
            className="w-56"
          />
        </div>
      </Card>

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
          errorTitle="Couldn’t run the expected-rebate analysis"
        >
          {(d) => (
            <div className="space-y-6">
              <BasisAndFraming
                priceBasis={d.price_basis}
                framing={d.legal_framing}
              />

              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <StatCard
                  label="Rebate absent from flagged lines"
                  value={decimalMoney(d.expected_rebate_eur, d.currency)}
                  sub="An advisory magnitude — what the usual rebate would have been worth"
                  accent="amber"
                />
                <Counter label="Lines examined" value={d.lines_examined} />
                <Counter
                  label="Lines carrying a rebate"
                  value={d.lines_with_a_rebate}
                  hint={`At or above the ${d.tolerance_eur_l} €/L threshold`}
                />
                <Counter
                  label="Lines with no expectation"
                  value={d.lines_without_an_expectation}
                  hint="No rebate history for that supplier and country yet"
                />
              </div>

              <p className="text-sm text-slate-600">{EXPECTED_REBATE_NOTE}</p>
              <p className="text-xs text-slate-400">
                A line counts as carrying no rebate when what was applied is
                below {d.tolerance_eur_l} €/L. A supplier and country pair with
                no rebate history produces nothing at all rather than a warning
                — with no history there is no expectation to measure against.
                {d.lines_skipped_zero_qty > 0 && (
                  <span className="ml-1">
                    {d.lines_skipped_zero_qty} line
                    {d.lines_skipped_zero_qty === 1 ? " was" : "s were"} skipped
                    for carrying no litres.
                  </span>
                )}
              </p>

              <Card
                title="What each supplier and country usually grants"
                padded={false}
              >
                {d.expectations.length === 0 ? (
                  <EmptyState
                    title="Nothing learned yet"
                    description="No supplier and country pair in scope has a history of rebated lines, so there is no typical rebate to compare this month against."
                  />
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <caption className="sr-only">
                        Typical rebate per supplier and country, learned from
                        history
                      </caption>
                      <thead>
                        <tr className="text-left text-xs text-slate-400">
                          <th className="px-5 py-2">Supplier</th>
                          <th className="px-5 py-2">Country</th>
                          <th className="px-5 py-2 text-right">Typical €/L</th>
                          <th className="px-5 py-2 text-right">Learned from</th>
                        </tr>
                      </thead>
                      <tbody>
                        {d.expectations.map((e) => (
                          <tr
                            key={`${e.supplier}-${e.country}`}
                            className="border-t border-slate-100"
                          >
                            <td className="px-5 py-2 font-medium text-slate-700">
                              {e.supplier}
                            </td>
                            <td className="px-5 py-2 text-slate-600">
                              {e.country}
                            </td>
                            <td className="px-5 py-2 text-right tabular-nums">
                              {e.typical_eur_l}
                            </td>
                            <td className="px-5 py-2 text-right tabular-nums text-slate-500">
                              {e.learned_from_lines} lines
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>

              <Card title="Lines that lost it" padded={false}>
                {d.findings.length === 0 ? (
                  <EmptyState
                    title="Every line in scope carries its usual rebate"
                    description="No line of this month came in below the learned rebate for its supplier and country."
                  />
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <caption className="sr-only">
                        Lines whose supplier and country pair has a learned
                        rebate and which carry none
                      </caption>
                      <thead>
                        <tr className="text-left text-xs text-slate-400">
                          <th className="px-5 py-2">Date</th>
                          <th className="px-5 py-2">Supplier</th>
                          <th className="px-5 py-2">Country</th>
                          <th className="px-5 py-2">Station</th>
                          <th className="px-5 py-2 text-right">Litres</th>
                          <th className="px-5 py-2 text-right">Invoiced €/L</th>
                          <th className="px-5 py-2 text-right">
                            Effective €/L
                          </th>
                          <th className="px-5 py-2 text-right">Applied €/L</th>
                          <th className="px-5 py-2 text-right">Typical €/L</th>
                          <th className="px-5 py-2 text-right">
                            Magnitude ({d.currency})
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {d.findings.map((f) => (
                          <tr
                            key={f.fuel_transaction_id}
                            className="border-t border-slate-100"
                          >
                            <td className="px-5 py-2 text-slate-600">
                              {f.txn_date}
                            </td>
                            <td className="px-5 py-2 font-medium text-slate-700">
                              {f.supplier}
                            </td>
                            <td className="px-5 py-2 text-slate-600">
                              {f.country}
                            </td>
                            <td className="px-5 py-2 text-slate-600">
                              {f.station}
                            </td>
                            <td className="px-5 py-2 text-right tabular-nums">
                              {f.litres}
                            </td>
                            <td className="px-5 py-2 text-right tabular-nums">
                              {f.eur_l_doc}
                            </td>
                            <td className="px-5 py-2 text-right tabular-nums">
                              {f.eur_l_eff}
                            </td>
                            <td className="px-5 py-2 text-right tabular-nums">
                              {f.applied_eur_l}
                            </td>
                            <td className="px-5 py-2 text-right tabular-nums">
                              {f.typical_eur_l}
                              <span className="ml-2 text-xs text-slate-400">
                                {f.learned_from_lines} lines
                              </span>
                            </td>
                            <td className="px-5 py-2 text-right font-medium tabular-nums">
                              {decimalMoney(f.expected_rebate_eur, d.currency)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
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
// Shared control
// ---------------------------------------------------------------------------

function CountryFilter({
  value,
  onChange,
  valid,
}: {
  value: string;
  onChange: (v: string) => void;
  valid: boolean;
}) {
  return (
    <Card title="Scope">
      <div className="flex flex-wrap items-end gap-4">
        <TextInput
          label="Country"
          hint="Two-letter country of supply — leave empty for all"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          maxLength={2}
          placeholder="All countries"
          className="w-40"
        />
        {!valid && (
          <p className="pb-2 text-xs text-slate-400">
            Use a two-letter country code, or leave it empty.
          </p>
        )}
      </div>
      <p className="mt-3 text-xs text-slate-400">
        Narrowing by country cannot change any comparison: every comparison is
        made inside one country already.
      </p>
    </Card>
  );
}
