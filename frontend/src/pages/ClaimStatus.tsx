import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
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
import {
  BASIS_NOTE,
  EMPTY_YEAR_BODY,
  EMPTY_YEAR_TITLE,
  NOT_SHOWN_NOTE,
  NO_FIGURE_NOTE,
  isYearShape,
  stageTone,
} from "../lib/transportClaimStatus";
import { isMappedRefusal } from "../lib/transportClaims";
import { useModules } from "../lib/useModules";
import type { ClientClaimStatus } from "../lib/types";

/**
 * The client claim-status portal (WO-93) — one read over
 * `GET /transport/claim-status?year=` (VAT_READ), and the one transport screen
 * written for the CLIENT rather than for the operator. Its question is a single
 * sentence — *"where are my claims?"* — and the design problem is entirely what
 * it must NOT answer.
 *
 * `BA_fleet_fuel.md` R39 states the requirement as three absences — plain
 * language stages ONLY, and nothing of the internal workflow vocabulary, of the
 * things an operator can DO to a claim, or of what we charge for the work. Its
 * acceptance line is itself an absence: a client-role session must not be able
 * to see any of them anywhere.
 *
 * FOUR THINGS THIS PAGE DOES NOT DO, EACH ASSERTED IN `e2e/claim-status.spec.ts`
 * RATHER THAN PROMISED (the word list the spec scans for lives THERE, in the
 * test, so that this file can be scanned against it):
 *
 * 1. **It renders no internal workflow identifier.** The 1A..5 vocabulary is
 *    translated on the SERVER and never crosses the wire, so there is nothing
 *    here to render even by accident — unlike `/recovery`, which deliberately
 *    shows an operator the raw state slug beside its label. The spec asserts
 *    that none of those values appears anywhere in the DOM.
 * 2. **It shows nothing about what we charge.** That is a real figure on a
 *    claim record and it has no business on the client's page about their own
 *    refund. The service never reads those columns and the wire carries no
 *    field for them, so there is nothing here to leak.
 * 3. **It offers no action.** There is not one button, form or menu on this
 *    page. A read-only view means no control at all, not a disabled one — every
 *    control this vertical has belongs to a role the client does not hold, so a
 *    control here could only ever be refused.
 * 4. **It invents no wording.** Every stage's name and explanation arrives on
 *    the wire and is rendered VERBATIM; `lib/transportClaimStatus.ts`
 *    deliberately holds no stage label at all. A friendlier synonym written
 *    here would be exactly the drift R39 exists to prevent, and the spec proves
 *    the label text exists in no file under `src/`.
 *
 * THE UI COMPUTES NOTHING (§4.9/§4.10). Every euro arrives as a decimal STRING,
 * already totalled and already quantized by the service with one ROUND_HALF_UP,
 * and renders through `decimalMoney` (string surgery, no arithmetic). There is
 * no arithmetic anywhere in this file — not a sum, not a product, not a
 * percentage, and no numeric conversion of a figure. Grep-provable, and the
 * spec greps.
 *
 * A `null` AMOUNT IS RENDERED AS A DASH, NEVER AS €0.00. The wire carries `null`
 * when no single-currency figure can be stated for a claim yet (its lines span
 * currencies, §4.14, or its frozen figure is absent). A zero would tell the
 * client their claim is worth nothing, which is a different and false
 * statement — the same distinction `/excise` makes for a country it holds no
 * rate for.
 *
 * READ-ONLY BY CONSTRUCTION: the service behind this page performs no write of
 * any kind, so opening the portal changes nothing about any claim (§4.19).
 */

/** The refund year to open on. `new Date()` is a clock read, not a money path —
 * the server owns the year's validation (`invalid_year`) and every figure. */
function thisYear(): string {
  return String(new Date().getFullYear());
}

export default function ClaimStatusPage() {
  const modules = useModules();
  const [year, setYear] = useState(thisYear);
  const enabled = modules.isEnabled("transport");
  const yearReady = isYearShape(year);

  const portal = useQuery<ClientClaimStatus>({
    queryKey: ["transport", "claim-status", year],
    queryFn: async () => (await api.get("/transport/claim-status", { params: { year } })).data,
    enabled: enabled && yearReady,
    retry: false,
  });

  if (modules.isLoading) return <Skeleton className="h-24 w-full" />;
  if (!enabled) {
    return (
      <div className="space-y-6">
        <PageHeader title="Claim status" />
        <ModuleInactive name={"Transport & VAT refunds"} />
      </div>
    );
  }

  // A REFUSAL and a FAILURE are different things and get different surfaces
  // (the `RecoveryDashboard.tsx` precedent): a refusal is the service saying no
  // for a reason we have a sentence for (`invalid_year`, `module_not_enabled`);
  // anything else — a 500, a network drop, a code newer than this build — is an
  // unexpected failure, and `QueryState`'s error state with its retry is the
  // honest presentation.
  const failureCode = portal.isError ? apiErrorCode(portal.error) : null;
  const refused = isMappedRefusal(failureCode);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Claim status"
        description="Where every one of your VAT refund claims stands right now, in plain language."
      />

      <Card title="Refund year">
        <div className="flex flex-wrap items-end gap-4">
          <TextInput
            label="Year"
            hint="The year the claims cover"
            value={year}
            onChange={(e) => setYear(e.target.value)}
            maxLength={4}
            placeholder="2026"
            className="w-32"
          />
          {!yearReady && (
            <p className="pb-2 text-xs text-slate-400">
              Type a four-digit year to see that year's claims.
            </p>
          )}
        </div>
      </Card>

      {refused && <RefusalNotice code={failureCode} detail={apiError(portal.error)} />}

      {!refused && (
        <QueryState
          query={portal}
          loading={<Skeleton className="h-40 w-full" />}
          errorTitle="Couldn’t load your claims"
        >
          {(d) => (
            <div className="space-y-6">
              {/* --------------- the stages, all six, always --------------- */}
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {d.stages.map((s) => (
                  <StatCard
                    key={s.stage}
                    label={s.label}
                    value={s.claims}
                    sub={s.description}
                    accent={s.claims > 0 && s.stage === "needs_attention" ? "rose" : "slate"}
                  />
                ))}
              </div>

              <p className="text-xs text-slate-400">{BASIS_NOTE}</p>

              {/* A year with no claims is a 200 with six zeroes, never a 404 and
                  never an error — so it is said in words rather than left to be
                  read off six zeroes, which look identical to a broken load. */}
              {d.total_claims === 0 && (
                <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
                  <p className="font-medium text-slate-700">{EMPTY_YEAR_TITLE}</p>
                  <p className="mt-1">{EMPTY_YEAR_BODY}</p>
                </div>
              )}

              {/* ---------------- the claims themselves ---------------- */}
              {d.claims.length > 0 && (
                <Card
                  title="Your claims"
                  padded={false}
                  actions={
                    <span className="text-xs text-slate-400">
                      {d.claims.length} claim{d.claims.length === 1 ? "" : "s"} in {d.year}
                    </span>
                  }
                >
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <caption className="sr-only">
                        Your VAT refund claims for {d.year}, by stage
                      </caption>
                      <thead>
                        <tr className="text-left text-xs text-slate-400">
                          <th className="px-5 py-2">Your company</th>
                          <th className="px-5 py-2">Refund country</th>
                          <th className="px-5 py-2">Period</th>
                          <th className="px-5 py-2">Where it stands</th>
                          <th className="px-5 py-2 text-right">VAT ({d.currency})</th>
                        </tr>
                      </thead>
                      <tbody>
                        {d.claims.map((c) => (
                          <tr
                            key={`${c.entity_name}|${c.refund_country}|${c.period}`}
                            className="border-t border-slate-100"
                          >
                            <td className="px-5 py-3 text-slate-700">{c.entity_name}</td>
                            <td className="px-5 py-3 text-slate-600">{c.refund_country}</td>
                            <td className="px-5 py-3 text-slate-600">{c.period}</td>
                            <td className="px-5 py-3">
                              <Badge tone={stageTone(c.stage)}>{stageLabel(d, c.stage)}</Badge>
                            </td>
                            <td className="px-5 py-3 text-right tabular-nums">
                              {/* `null` renders as a dash, never as €0.00 —
                                  see the module docstring. */}
                              {c.vat_eur === null ? "—" : decimalMoney(c.vat_eur, d.currency)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <p className="px-5 py-3 text-xs text-slate-400">{NO_FIGURE_NOTE}</p>
                </Card>
              )}

              {/* The count exists so the arithmetic is honest; the reason a
                  claim left the ladder is internal and is not named here. */}
              {d.not_shown_claims > 0 && (
                <p role="status" className="text-xs text-slate-400">
                  {NOT_SHOWN_NOTE}
                </p>
              )}
            </div>
          )}
        </QueryState>
      )}
    </div>
  );
}

/**
 * The stage's plain-language name, looked up in THIS RESPONSE's own stage list.
 * The wording is the server's and this page holds none of it — a claim row
 * carries the slug, and the stage list carries the words for it. An unmatched
 * slug (a stage added server-side that this build's response somehow lacks)
 * falls back to the slug itself rather than to an invented label: showing the
 * raw value is honest, inventing a friendly one is not.
 */
function stageLabel(view: ClientClaimStatus, stage: string): string {
  return view.stages.find((s) => s.stage === stage)?.label ?? stage;
}
