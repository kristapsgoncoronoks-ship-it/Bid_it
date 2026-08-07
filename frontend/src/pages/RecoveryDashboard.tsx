import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { ModuleInactive } from "../components/ModuleGate";
import { RefusalNotice } from "../components/RefusalNotice";
import {
  Badge,
  Card,
  PageHeader,
  QueryState,
  Skeleton,
  StatCard,
  TextInput,
} from "../components/ui";
import { api, apiError, apiErrorCode } from "../lib/api";
import { decimalMoney } from "../lib/format";
import { isMappedRefusal } from "../lib/transportClaims";
import { bucketCopy, excludedCopy, isYearShape } from "../lib/transportRecovery";
import { useModules } from "../lib/useModules";
import type { RecoveryDashboard } from "../lib/types";

/**
 * The cash-recovery dashboard (WO-86) — one read over
 * `GET /transport/recovery-dashboard?year=` (TRANSPORT_READ), the first
 * transport screen that answers a question about the PORTFOLIO rather than
 * about one claim: *how much money can we still recover this year, and what is
 * stopping each euro of it?*
 *
 * READ-ONLY BY CONSTRUCTION. There is no mutating control on this page at all,
 * because the service behind it performs no write of any kind — opening the
 * dashboard changes nothing about any claim (master-context §4.19). Every
 * action here is a link to the screen that owns the fix.
 *
 * THE UI COMPUTES NOTHING. Every euro, every count and the median arrive
 * already totalled and already quantized by the service, and are rendered from
 * the wire string exactly as received (§4.9/§4.10). This page performs no
 * addition, no percentage and no rounding — a UI-side total would be a second,
 * forkable source of truth for a figure the whole commercial model runs on.
 *
 * FOUR PRESENTATION DECISIONS, EACH FOR A STATED REASON:
 *
 * 1. **All six buckets always render, including the empty ones.** The service
 *    always sends all six for the reason its own schema states: a bucket that
 *    vanished at zero would make "nothing is at deadline risk" and "the
 *    dashboard forgot to tell you" look identical.
 *
 * 2. **Deadline risk sits OUTSIDE the bucket table.** WO-81 interpretation 3
 *    deliberately did not make it a seventh bucket: a claim inside the 60-day
 *    window keeps its blocking bucket (which says WHAT TO DO) and is counted
 *    here as well (which says HOW URGENT). Rendering it as a seventh row would
 *    both double-count and destroy that distinction.
 *
 * 3. **A null median is "not yet measurable", never 0.** `median_days_to_refund`
 *    is null until a paid claim carries BOTH a submitted and a paid date, and
 *    the service's own docstring gives the reason it refuses to emit 0: it
 *    "would claim refunds arrive the same day they are filed". The sample size
 *    is shown beside it so the null is legible rather than mysterious.
 *
 * 4. **A non-zero `currency_mismatch_claims` gets a named notice.** Those drafts
 *    span currencies, so the service excludes them from every euro above rather
 *    than summing across currencies (§4.14) — and their exclusion is exactly why
 *    a claimable total can look short. Hiding the count would leave the operator
 *    with an unexplained shortfall; at zero the notice is absent, so its
 *    presence always means something.
 *
 * `overcharges_eur` is rendered as a SEPARATE cash stream, deliberately outside
 * the recovered + awaiting + claimable identity: it is booked cash recovered
 * from SUPPLIERS for contract breaches, not this year's VAT, and folding it in
 * would make that identity false (the schema comment says so in as many words).
 */

/** The refund year to open on. `new Date()` is a clock read, not a money path —
 * the server owns the year's validation (`invalid_year`) and every figure. */
function thisYear(): string {
  return String(new Date().getFullYear());
}

export default function RecoveryDashboardPage() {
  const modules = useModules();
  const [year, setYear] = useState(thisYear);
  const enabled = modules.isEnabled("transport");
  const yearReady = isYearShape(year);

  const dash = useQuery<RecoveryDashboard>({
    queryKey: ["transport", "recovery-dashboard", year],
    queryFn: async () =>
      (await api.get("/transport/recovery-dashboard", { params: { year } })).data,
    enabled: enabled && yearReady,
    retry: false,
  });

  if (modules.isLoading) return <Skeleton className="h-24 w-full" />;
  if (!enabled) {
    return (
      <div className="space-y-6">
        <PageHeader title="Cash recovery" />
        <ModuleInactive name={"Transport & VAT refunds"} />
      </div>
    );
  }

  // A REFUSAL and a FAILURE are different things and get different surfaces.
  // A refusal is the service saying no for a reason we have a sentence for
  // (`invalid_year`, `module_not_enabled`): show the sentence and the action.
  // Anything else — a 500, a network drop, a code newer than this build — is an
  // unexpected failure, and `QueryState`'s error state with its retry is the
  // honest presentation. Branching on "is it mapped" rather than "is there a
  // code" is what keeps a mocked/unknown server error out of the refusal lane.
  const failureCode = dash.isError ? apiErrorCode(dash.error) : null;
  const refused = isMappedRefusal(failureCode);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Cash recovery"
        description="Every VAT refund claim of one year, bucketed by what is stopping it, with the euros beside it. Amounts are net EUR, exactly as the service totalled them — this page adds nothing up."
      />

      <Card title="Refund year">
        <div className="flex flex-wrap items-end gap-4">
          <TextInput
            label="Year"
            hint="The claim's own reference-period year"
            value={year}
            onChange={(e) => setYear(e.target.value)}
            maxLength={4}
            placeholder="2026"
            className="w-32"
          />
          {!yearReady && (
            <p className="pb-2 text-xs text-slate-400">Type a four-digit year to load the portfolio.</p>
          )}
        </div>
      </Card>

      {refused && <RefusalNotice code={failureCode} detail={apiError(dash.error)} />}

      {!refused && (
      <QueryState
        query={dash}
        loading={<Skeleton className="h-40 w-full" />}
        errorTitle="Couldn’t load the recovery dashboard"
      >
        {(d) => (
          <div className="space-y-6">
            {/* ---------------- the north-star euros ---------------- */}
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <StatCard
                label="Recovered"
                value={decimalMoney(d.recovered_eur, d.currency)}
                sub="Refunds received on this year's claims"
                accent="emerald"
              />
              <StatCard
                label="Awaiting refund"
                value={decimalMoney(d.awaiting_eur, d.currency)}
                sub="Filed and with the refunding member state"
                accent="brand"
              />
              <StatCard
                label="Still claimable"
                value={decimalMoney(d.claimable_eur, d.currency)}
                sub="Across every draft claim, blocked or not"
                accent="amber"
              />
              <StatCard
                label="Deadline risk"
                value={d.deadline_risk_claims}
                sub={
                  d.deadline_risk_claims > 0
                    ? "Unfiled claims within 60 days of the 30 September cut-off"
                    : "No unfiled claim is inside the 60-day window"
                }
                accent={d.deadline_risk_claims > 0 ? "rose" : "slate"}
              />
            </div>

            <p className="text-xs text-slate-400">
              Recovered, awaiting and still-claimable are the whole of this year's VAT: every
              bucketed claim's VAT sits in exactly one of them. The deadline-risk count is
              deliberately not a seventh bucket — a claim inside the window keeps whichever bucket
              says what to do about it, and is counted here as well to say how urgent it is.
            </p>

            {/* A year with no claims is a 200 with zeroes, never a 404 and never
                an error — so it is said in words rather than left to be read
                off six zeroes, which look identical to a broken load. */}
            {d.total_claims === 0 && (
              <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
                <p className="font-medium text-slate-700">No refund claims in {d.year} yet</p>
                <p className="mt-1">
                  Nothing is at risk and nothing is recoverable because no claim has been opened
                  for this year. Start one in the{" "}
                  <Link to="/vat-claims" className="font-medium text-brand-600 hover:underline">
                    claims workspace
                  </Link>
                  , or pick a different year above.
                </p>
              </div>
            )}

            {/* -------- §4.14: the claims that contribute nothing -------- */}
            {d.currency_mismatch_claims > 0 && (
              <div
                role="status"
                className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900"
              >
                <p className="font-semibold">
                  {d.currency_mismatch_claims} draft{" "}
                  {d.currency_mismatch_claims === 1 ? "claim spans" : "claims span"} more than one
                  currency
                </p>
                <p className="mt-1 text-amber-800">
                  A claim is filed in a single currency, so these contribute {decimalMoney("0.00", d.currency)}{" "}
                  to every euro above rather than being summed across currencies. That is why a
                  total here can look short. Open each claim and split it by currency, or correct
                  the source transactions, and the euros reappear.
                </p>
              </div>
            )}

            {/* ---------------- the six readiness buckets ---------------- */}
            <Card
              title="Readiness"
              padded={false}
              actions={
                <span className="text-xs text-slate-400">
                  {d.total_claims} claim{d.total_claims === 1 ? "" : "s"} in {d.year}
                </span>
              }
            >
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <caption className="sr-only">
                    VAT refund claims of {d.year} by readiness state
                  </caption>
                  <thead>
                    <tr className="text-left text-xs text-slate-400">
                      <th className="px-5 py-2">State</th>
                      <th className="px-5 py-2">What it means</th>
                      <th className="px-5 py-2 text-right">Claims</th>
                      <th className="px-5 py-2 text-right">VAT ({d.currency})</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.buckets.map((b) => {
                      const copy = bucketCopy(b.state);
                      return (
                        <tr key={b.state} className="border-t border-slate-100 align-top">
                          <td className="px-5 py-3">
                            <div className="flex items-center gap-2">
                              <Badge tone={copy.tone}>{copy.label}</Badge>
                              <span className="font-mono text-xs text-slate-400">{b.state}</span>
                            </div>
                          </td>
                          <td className="px-5 py-3 text-slate-600">
                            <p>{copy.meaning}</p>
                            <p className="mt-1 text-xs text-slate-400">{copy.next}</p>
                          </td>
                          <td className="px-5 py-3 text-right tabular-nums">{b.claims}</td>
                          <td className="px-5 py-3 text-right font-medium tabular-nums">
                            {decimalMoney(b.vat_eur, d.currency)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <p className="border-t border-slate-100 px-5 py-3 text-xs text-slate-400">
                Every claim of {d.year} appears in exactly one row above or in the excluded list
                below. Open the claims workspace to act on any of them —{" "}
                <Link to="/vat-claims" className="font-medium text-brand-600 hover:underline">
                  VAT claims
                </Link>
                .
              </p>
            </Card>

            {/* ---------------- excluded + the second stream ---------------- */}
            <div className="grid gap-4 lg:grid-cols-2">
              <Card title="Not in any readiness state">
                {d.excluded.length === 0 ? (
                  <p className="text-sm text-slate-500">
                    No claim of {d.year} was withdrawn or rejected.
                  </p>
                ) : (
                  <ul className="space-y-3 text-sm">
                    {d.excluded.map((x) => (
                      <li key={x.reason} className="flex items-start justify-between gap-4">
                        <div>
                          <p className="font-medium text-slate-700">{x.reason}</p>
                          <p className="mt-0.5 text-xs text-slate-400">{excludedCopy(x.reason)}</p>
                        </div>
                        <span className="tabular-nums text-slate-600">{x.claims}</span>
                      </li>
                    ))}
                  </ul>
                )}
                <p className="mt-4 text-xs text-slate-400">
                  These carry no recoverable euro, so they are listed rather than bucketed — and
                  listed rather than dropped, so the claim count stays honest.
                </p>
              </Card>

              <Card title="Beside the VAT: what suppliers owe us">
                <div className="flex items-baseline gap-3">
                  <span className="text-2xl font-semibold tabular-nums text-slate-800">
                    {decimalMoney(d.overcharges_eur, d.currency)}
                  </span>
                  <span className="text-xs text-slate-400">recovered from suppliers</span>
                </div>
                <p className="mt-3 text-sm text-slate-600">
                  Booked cash credited back by suppliers for contract breaches — a second recovery
                  stream, deliberately kept out of the VAT totals above so those still add up to
                  this year's VAT and nothing else.
                </p>
                <p className="mt-3 text-sm">
                  <Link to="/overcharges" className="font-medium text-brand-600 hover:underline">
                    Open supplier overcharges
                  </Link>
                </p>
              </Card>
            </div>

            {/* ---------------- the honest median ---------------- */}
            <Card title="How long a refund takes">
              {d.median_days_to_refund === null ? (
                <>
                  <p className="text-lg font-semibold text-slate-700">Not yet measurable</p>
                  <p className="mt-2 text-sm text-slate-600">
                    {`Measuring this needs a refunded claim carrying both the date it was filed and the date the money arrived. So far ${d.days_to_refund_sample} ${d.days_to_refund_sample === 1 ? "claim carries" : "claims carry"} both dates in ${d.year}, so there is no median to report — which is a different fact from a refund arriving the same day it is filed.`}
                  </p>
                </>
              ) : (
                <>
                  <p className="text-lg font-semibold tabular-nums text-slate-800">
                    {d.median_days_to_refund} days
                  </p>
                  <p className="mt-2 text-sm text-slate-600">
                    {`Median time from filing to the money arriving, over ${d.days_to_refund_sample} refunded ${d.days_to_refund_sample === 1 ? "claim" : "claims"} of ${d.year} carrying both dates.`}
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
