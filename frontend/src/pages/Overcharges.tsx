import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { ModuleInactive } from "../components/ModuleGate";
import { RefusalNotice } from "../components/RefusalNotice";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Modal,
  PageHeader,
  QueryState,
  Skeleton,
  TabPanel,
  Tabs,
  TextInput,
} from "../components/ui";
import { api, apiError, apiErrorCode, downloadFile } from "../lib/api";
import { decimalMoney, shortDate } from "../lib/format";
import { hasVatPerm } from "../lib/roles";
import {
  BREACH_FLAG_COPY,
  OVERCHARGE_STATUS_COPY,
  RECOVERED_STATE,
  isDecimalShape,
  isPeriodShape,
  isTerminal,
  IGNORED_STATE,
  overchargeActions,
  statusTone,
} from "../lib/transportRecovery";
import { useModules } from "../lib/useModules";
import type {
  ContractAudit,
  ContractTerm,
  OverchargeClaim,
  OverchargeTotal,
} from "../lib/types";
import { useConfirm } from "../components/ui/useConfirm";

/**
 * The supplier-overcharge workspace (WO-86) — one tabbed page over the WO-82
 * detection/claim-back routes and the WO-83 artifacts, which until now had no
 * UI at all.
 *
 * Three panels, each reading and writing exactly one service surface:
 *   1. Detection     — `GET /transport/overcharges/audit` (read-only)
 *   2. Claim-backs   — the worklist, the lifecycle, and the two artifacts
 *   3. Contract terms— `GET/PUT/DELETE /transport/contract-terms`
 *
 * PERMISSIONS ARE MIRRORED, NOT ENFORCED HERE. Reads need `transport.read` (the
 * router-level default); the mutations — opening a claim-back, advancing it,
 * setting or removing a term — are `vat.write`, which is exactly how
 * `overcharges.py` declares them. Two consequences worth stating because they
 * are easy to get wrong:
 *   - The artifact DOWNLOADS are reads. `overcharges.py` says so explicitly
 *     ("generating an artifact is a read-only rendering of the analytics this
 *     router already serves as JSON"), so they stay visible to a read-only role
 *     rather than being hidden with the write verbs.
 *   - A hidden control is never a security boundary (master-context §6). The
 *     server is the control, and a 403 it returns still renders through
 *     `RefusalNotice`.
 *
 * ADVISORY VS BLOCKING — THE DISTINCTION THIS PAGE MUST NOT BLUR (§4.19):
 *   - **Detection is advisory and read-only.** Running it writes nothing, gates
 *     nothing and changes no figure. `source_warnings` is likewise advisory: it
 *     says a supplier is being priced at LIST because its rebate document is
 *     missing, and it blocks nothing.
 *   - **The artifact refusals BLOCK, and the copy says so.** An
 *     `overcharge_evidence_drift` means no packet and no letter are produced —
 *     the page offers no way round it, because sending a demand its own
 *     attachment contradicts is the exact failure the refusal exists to prevent.
 *
 * TWO EUROS, TWO MEANINGS, NEVER ONE LABEL. `recover_eur` on the audit is the
 * €-exposure DETECTED; `recovered_eur` on the total is BOOKED CASH. The service
 * keeps them apart deliberately and so does this page — they never share a tile,
 * a column heading or a sentence.
 *
 * NO ARITHMETIC. Every euro and every €/L rate is rendered from the wire string
 * through `decimalMoney` and posted back as the typed string; the service is the
 * one place that totals and quantizes (§4.9/§4.10).
 */

type TabKey = "detection" | "claims" | "terms";

const TABS: { value: TabKey; label: string }[] = [
  { value: "detection", label: "Detection" },
  { value: "claims", label: "Claim-backs" },
  { value: "terms", label: "Contract terms" },
];

/** The current accounting month, "YYYY-MM". A clock read, never a money path. */
function currentPeriod(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

interface Refusal {
  code: string | null;
  detail: string;
}

export default function OverchargesPage() {
  const qc = useQueryClient();
  const { user } = useAuth();
  const modules = useModules();
  const canWrite = hasVatPerm(user, "vat.write"); // cosmetic — the server enforces it
  const [tab, setTab] = useState<TabKey>("detection");
  const [refusal, setRefusal] = useState<Refusal | null>(null);

  const enabled = modules.isEnabled("transport");
  const onRefusal = (e: unknown) =>
    setRefusal({ code: apiErrorCode(e), detail: apiError(e) });

  if (modules.isLoading) return <Skeleton className="h-24 w-full" />;
  if (!enabled) {
    return (
      <div className="space-y-6">
        <PageHeader title="Supplier overcharges" />
        <ModuleInactive name={"Transport & VAT refunds"} />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Supplier overcharges"
        description="What a supplier owes us for breaching the agreed commercial terms — detected over the validated fuel lines, then claimed back. Amounts are net EUR, exactly as the service computed them."
      />

      {refusal && (
        <RefusalNotice
          code={refusal.code}
          detail={refusal.detail}
          onDismiss={() => setRefusal(null)}
          periodShape="month"
        />
      )}

      <Tabs
        tabs={TABS}
        value={tab}
        onChange={(v) => {
          setTab(v as TabKey);
          setRefusal(null);
        }}
        label="Overcharge workspace sections"
        idBase="overcharges"
      />

      {tab === "detection" && (
        <TabPanel idBase="overcharges" value="detection">
          <DetectionPanel
            canWrite={canWrite}
            onRefusal={onRefusal}
            onOpened={() => {
              setRefusal(null);
              qc.invalidateQueries({ queryKey: ["transport", "overcharges"] });
              setTab("claims");
            }}
          />
        </TabPanel>
      )}
      {tab === "claims" && (
        <TabPanel idBase="overcharges" value="claims">
          <ClaimBackPanel
            canWrite={canWrite}
            onRefusal={onRefusal}
            clearRefusal={() => setRefusal(null)}
          />
        </TabPanel>
      )}
      {tab === "terms" && (
        <TabPanel idBase="overcharges" value="terms">
          <ContractTermsPanel
            canWrite={canWrite}
            onRefusal={onRefusal}
            clearRefusal={() => setRefusal(null)}
          />
        </TabPanel>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 1. Detection — read-only over `GET /transport/overcharges/audit`
// ---------------------------------------------------------------------------

function DetectionPanel({
  canWrite,
  onRefusal,
  onOpened,
}: {
  canWrite: boolean;
  onRefusal: (e: unknown) => void;
  onOpened: () => void;
}) {
  const [period, setPeriod] = useState(currentPeriod);
  const [supplier, setSupplier] = useState("");
  const ready = isPeriodShape(period);

  const audit = useQuery<ContractAudit>({
    queryKey: ["transport", "overcharge-audit", period, supplier],
    queryFn: async () =>
      (
        await api.get("/transport/overcharges/audit", {
          params: {
            period,
            ...(supplier.trim() ? { supplier: supplier.trim() } : {}),
          },
        })
      ).data,
    enabled: ready,
    retry: false,
  });

  const open = useMutation({
    mutationFn: async (vars: { supplier: string; period: string }) =>
      (await api.post("/transport/overcharges", vars)).data as OverchargeClaim,
    onSuccess: onOpened,
    onError: onRefusal,
  });

  return (
    <div className="space-y-4">
      <Card title="Audit a period against the agreed terms">
        <div className="grid gap-4 sm:grid-cols-3">
          <TextInput
            label="Accounting month"
            hint="YYYY-MM"
            value={period}
            onChange={(e) => setPeriod(e.target.value)}
            placeholder="2026-05"
          />
          <TextInput
            label="Supplier"
            hint="Optional — the fuel-card or toll-network code"
            value={supplier}
            onChange={(e) => setSupplier(e.target.value)}
            placeholder="Every supplier"
          />
        </div>
        <p className="mt-3 text-xs text-slate-400">
          Running this audit reads the validated fuel lines and writes nothing.
          It changes no figure, blocks no claim and halts no close — it tells
          you what to go and ask for.
        </p>
      </Card>

      <QueryState
        query={audit}
        loading={<Skeleton className="h-32 w-full" />}
        errorTitle="Couldn’t run the contract audit"
      >
        {(a) => (
          <div className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-3">
              <Card title="Detected exposure">
                <p className="text-2xl font-semibold tabular-nums text-slate-800">
                  {decimalMoney(a.recover_eur, a.currency)}
                </p>
                <p className="mt-1 text-xs text-slate-400">
                  What the breaches add up to across {a.breaches.length} line
                  {a.breaches.length === 1 ? "" : "s"} — not cash recovered.
                </p>
              </Card>
              <Card title="Lines audited">
                <p className="text-2xl font-semibold tabular-nums text-slate-800">
                  {a.lines_audited}
                </p>
                <p className="mt-1 text-xs text-slate-400">
                  {a.lines_without_terms} had no agreed term;{" "}
                  {a.lines_skipped_zero_qty} carried no litres.
                </p>
              </Card>
              <Card title="Basis">
                <p className="text-sm font-medium text-slate-700">
                  {a.price_basis}
                </p>
                <p className="mt-2 text-xs text-slate-500">{a.legal_framing}</p>
              </Card>
            </div>

            {/* R50's source guard — ADVISORY. It never blocks and changes no
                euro; it says the price below is a LIST price for that supplier
                because the rebate document has not been recorded. */}
            {a.source_warnings.length > 0 && (
              <div
                role="status"
                className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900"
              >
                <p className="font-semibold">
                  Some suppliers are being priced at list because a rebate
                  document is missing
                </p>
                <p className="mt-1 text-amber-800">
                  These have paid an off-invoice rebate in an earlier period but
                  none is recorded for {a.period}:{" "}
                  <span className="font-mono">
                    {a.source_warnings.join(", ")}
                  </span>
                  . Their effective price below is therefore not effective, and
                  a demand built on it could ask for money already paid. This is
                  a warning only — it blocks nothing and changes no figure.
                  Record the document under{" "}
                  <Link to="/rebates" className="font-medium underline">
                    off-invoice rebates
                  </Link>
                  .
                </p>
              </div>
            )}

            {a.lines_without_terms > 0 && a.breaches.length === 0 && (
              <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
                <p className="font-medium text-slate-700">
                  No breach found — but {a.lines_without_terms} line
                  {a.lines_without_terms === 1 ? " has" : "s have"} no agreed
                  term to check against
                </p>
                <p className="mt-1">
                  “We have not told the system what was agreed” is not “the
                  supplier honoured the contract”. Add the terms under Contract
                  terms before reading this as a clean period.
                </p>
              </div>
            )}

            <Card
              title={`Breaches in ${a.period}`}
              padded={false}
              actions={
                canWrite && a.breaches.length > 0 && a.supplier ? (
                  <Button
                    size="sm"
                    onClick={() =>
                      open.mutate({
                        supplier: a.supplier as string,
                        period: a.period,
                      })
                    }
                    loading={open.isPending}
                  >
                    Open a claim-back
                  </Button>
                ) : null
              }
            >
              {a.breaches.length === 0 ? (
                <EmptyState
                  title="No breach detected in this period"
                  description="Every audited line met the agreed discount and stayed under the agreed ceiling."
                />
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <caption className="sr-only">
                      Contract breaches in {a.period}
                    </caption>
                    <thead>
                      <tr className="text-left text-xs text-slate-400">
                        <th className="px-5 py-2">Date</th>
                        <th className="px-5 py-2">Supplier</th>
                        <th className="px-5 py-2">Country</th>
                        <th className="px-5 py-2">Station</th>
                        <th className="px-5 py-2">Product</th>
                        <th className="px-5 py-2">Breach</th>
                        <th className="px-5 py-2 text-right">Litres</th>
                        <th className="px-5 py-2 text-right">Agreed €/L</th>
                        <th className="px-5 py-2 text-right">Actual €/L</th>
                        <th className="px-5 py-2 text-right">Gap €/L</th>
                        <th className="px-5 py-2 text-right">Recoverable</th>
                      </tr>
                    </thead>
                    <tbody>
                      {a.breaches.map((b) => (
                        <tr
                          key={b.fuel_transaction_id}
                          className="border-t border-slate-100"
                        >
                          <td className="px-5 py-2 text-xs text-slate-500">
                            {shortDate(b.txn_date)}
                          </td>
                          <td className="px-5 py-2">{b.supplier}</td>
                          <td className="px-5 py-2 font-mono text-xs">
                            {b.country}
                          </td>
                          <td className="px-5 py-2 text-xs text-slate-500">
                            {b.station}
                          </td>
                          <td className="px-5 py-2 text-xs">
                            {b.product_group}
                          </td>
                          <td className="px-5 py-2">
                            <Badge tone="warning">{b.flag}</Badge>
                            <span className="mt-0.5 block text-xs text-slate-400">
                              {BREACH_FLAG_COPY[b.flag] ?? ""}
                            </span>
                          </td>
                          <td className="px-5 py-2 text-right tabular-nums">
                            {b.qty}
                          </td>
                          <td className="px-5 py-2 text-right tabular-nums">
                            {b.agreed_eur_l}
                          </td>
                          <td className="px-5 py-2 text-right tabular-nums">
                            {b.actual_eur_l}
                          </td>
                          <td className="px-5 py-2 text-right tabular-nums">
                            {b.gap_eur_l}
                          </td>
                          <td className="px-5 py-2 text-right font-medium tabular-nums">
                            {decimalMoney(b.recover_eur, a.currency)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <p className="border-t border-slate-100 px-5 py-3 text-xs text-slate-400">
                €/L figures are the basis stated above. A line's own document
                currency is provenance and is never added to anything — every
                euro here is EUR.
              </p>
            </Card>
          </div>
        )}
      </QueryState>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 2. Claim-backs — the worklist, the lifecycle and the two artifacts
// ---------------------------------------------------------------------------

function ClaimBackPanel({
  canWrite,
  onRefusal,
  clearRefusal,
}: {
  canWrite: boolean;
  onRefusal: (e: unknown) => void;
  clearRefusal: () => void;
}) {
  const qc = useQueryClient();
  const [advancing, setAdvancing] = useState<{
    claim: OverchargeClaim;
    to: string;
  } | null>(null);
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");

  const claims = useQuery<OverchargeClaim[]>({
    queryKey: ["transport", "overcharges"],
    queryFn: async () => (await api.get("/transport/overcharges")).data,
    retry: false,
  });

  const total = useQuery<OverchargeTotal>({
    queryKey: ["transport", "overcharges", "total"],
    queryFn: async () => (await api.get("/transport/overcharges/total")).data,
    retry: false,
  });

  const advance = useMutation({
    mutationFn: async (vars: {
      id: string;
      to_status: string;
      recovered_eur?: string;
      note?: string;
    }) =>
      (
        await api.post(`/transport/overcharges/${vars.id}/advance`, {
          to_status: vars.to_status,
          ...(vars.recovered_eur ? { recovered_eur: vars.recovered_eur } : {}),
          ...(vars.note ? { note: vars.note } : {}),
        })
      ).data as OverchargeClaim,
    onSuccess: () => {
      clearRefusal();
      setAdvancing(null);
      setAmount("");
      setNote("");
      qc.invalidateQueries({ queryKey: ["transport", "overcharges"] });
    },
    onError: onRefusal,
  });

  // The artifacts are READS (the router-level TRANSPORT_READ), so they are not
  // hidden behind `canWrite`. `downloadFile` re-inflates a blob error body into
  // the {detail, code} shape, which is what lets a refusal render a sentence.
  const download = async (
    claim: OverchargeClaim,
    kind: "packet" | "letter",
  ) => {
    clearRefusal();
    const ext = kind === "packet" ? "xlsx" : "pdf";
    try {
      await downloadFile(
        `/transport/overcharges/${claim.id}/${kind}`,
        `overcharge-${claim.id}-${kind === "packet" ? "evidence" : "letter"}.${ext}`,
      );
    } catch (e) {
      onRefusal(e);
    }
  };

  const amountNeeded = advancing?.to === RECOVERED_STATE;
  // §12 (WO-L): ignoring is explicit and REASONED — the server refuses an
  // ignore without a note (`ignore_reason_required`), so the button waits.
  const reasonNeeded = advancing?.to === IGNORED_STATE;
  const advanceReady =
    (!amountNeeded || isDecimalShape(amount)) && (!reasonNeeded || note.trim().length > 0);

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <Card title="Booked cash recovered from suppliers">
          {total.data ? (
            <>
              <p className="text-2xl font-semibold tabular-nums text-emerald-600">
                {decimalMoney(total.data.recovered_eur, total.data.currency)}
              </p>
              <p className="mt-1 text-xs text-slate-400">
                Actually credited back by suppliers — not the exposure a
                contract audit detects. The two are never reported under one
                name.
              </p>
            </>
          ) : (
            <Skeleton className="h-10 w-32" />
          )}
        </Card>
        <Card title="How a claim-back moves">
          <p className="text-sm text-slate-600">
            detected → packaged → claimed → recovered, rejected or written off.
          </p>
          <p className="mt-2 text-xs text-slate-400">
            One step at a time, and the last three are final. The euro demanded
            is frozen when the claim-back is opened and is never re-derived — it
            is the figure the supplier was sent.
          </p>
        </Card>
      </div>

      <Card title="Claim-backs" padded={false}>
        <QueryState
          query={claims}
          loading={<Skeleton className="m-5 h-24 w-full" />}
          isEmpty={(rows) => rows.length === 0}
          empty={
            <EmptyState
              title="No claim-backs opened yet"
              description="Run a contract audit under Detection; where it finds a breach you can open the claim-back that freezes the euro to demand."
            />
          }
          errorTitle="Couldn’t load the claim-backs"
        >
          {(rows) => (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <caption className="sr-only">
                  Supplier overcharge claim-backs
                </caption>
                <thead>
                  <tr className="text-left text-xs text-slate-400">
                    <th className="px-5 py-2">Supplier</th>
                    <th className="px-5 py-2">Period</th>
                    <th className="px-5 py-2">State</th>
                    <th className="px-5 py-2 text-right">Demanded</th>
                    <th className="px-5 py-2 text-right">Lines</th>
                    <th className="px-5 py-2 text-right">Recovered</th>
                    <th className="px-5 py-2">Opened</th>
                    <th className="px-5 py-2">Documents</th>
                    <th className="px-5 py-2">Next step</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((c) => {
                    const actions = overchargeActions(c.status);
                    return (
                      <tr
                        key={c.id}
                        className="border-t border-slate-100 align-top"
                      >
                        <td className="px-5 py-3">{c.supplier}</td>
                        <td className="px-5 py-3 font-mono text-xs">
                          {c.period}
                        </td>
                        <td className="px-5 py-3">
                          <Badge tone={statusTone(c.status)}>{c.status}</Badge>
                          <span className="mt-0.5 block max-w-56 text-xs text-slate-400">
                            {OVERCHARGE_STATUS_COPY[c.status] ?? ""}
                          </span>
                        </td>
                        <td className="px-5 py-3 text-right font-medium tabular-nums">
                          {decimalMoney(c.detected_eur, c.currency)}
                        </td>
                        <td className="px-5 py-3 text-right tabular-nums">
                          {c.lines_count}
                        </td>
                        <td className="px-5 py-3 text-right tabular-nums">
                          {decimalMoney(c.recovered_eur, c.currency)}
                        </td>
                        <td className="px-5 py-3 text-xs text-slate-500">
                          {shortDate(c.created_at)}
                        </td>
                        <td className="px-5 py-3">
                          <div className="flex flex-col items-start gap-1">
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => download(c, "packet")}
                            >
                              Evidence packet
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => download(c, "letter")}
                            >
                              Claim letter
                            </Button>
                          </div>
                        </td>
                        <td className="px-5 py-3">
                          {isTerminal(c.status) ? (
                            <span className="text-xs text-slate-400">
                              Closed — no further step.
                            </span>
                          ) : canWrite ? (
                            <div className="flex flex-wrap gap-1">
                              {actions.map((to) => (
                                <Button
                                  key={to}
                                  size="sm"
                                  variant="secondary"
                                  onClick={() => {
                                    clearRefusal();
                                    setAmount("");
                                    setNote("");
                                    setAdvancing({ claim: c, to });
                                  }}
                                >
                                  {to.replace("_", " ")}
                                </Button>
                              ))}
                            </div>
                          ) : (
                            <span className="text-xs text-slate-400">
                              {actions.join(", ").replace(/_/g, " ")}
                            </span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </QueryState>
        <p className="border-t border-slate-100 px-5 py-3 text-xs text-slate-400">
          Both documents render from the same lines and the same total. If a
          recorded rebate has since changed the effective price those lines are
          audited against, neither document is produced — a demand its own
          attachment contradicts is exactly what that refusal prevents.
        </p>
      </Card>

      <Modal
        open={advancing !== null}
        onClose={() => setAdvancing(null)}
        title={
          advancing
            ? `Move ${advancing.claim.supplier} ${advancing.claim.period} to “${advancing.to.replace("_", " ")}”`
            : ""
        }
      >
        {advancing && (
          <div className="space-y-4">
            {amountNeeded ? (
              <>
                <TextInput
                  label="Amount the supplier credited"
                  hint={`EUR, no more than the ${decimalMoney(advancing.claim.detected_eur, advancing.claim.currency)} demanded`}
                  required
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                  placeholder="0.00"
                />
                <p className="text-xs text-slate-400">
                  This is the figure that books into the recovered total, so it
                  is never assumed from the demand — type what actually arrived.
                </p>
              </>
            ) : (
              <p className="text-sm text-slate-600">
                No amount is recorded for this move — only a recovered
                claim-back books cash.
              </p>
            )}
            <TextInput
              label={reasonNeeded ? "Reason" : "Note"}
              hint={
                reasonNeeded
                  ? "Required — why this claim-back is not being pursued (kept on the audit record)"
                  : "Optional — kept on the claim-back"
              }
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setAdvancing(null)}>
                Cancel
              </Button>
              <Button
                disabled={!advanceReady}
                loading={advance.isPending}
                onClick={() =>
                  advance.mutate({
                    id: advancing.claim.id,
                    to_status: advancing.to,
                    ...(amountNeeded ? { recovered_eur: amount.trim() } : {}),
                    ...(note.trim() ? { note: note.trim() } : {}),
                  })
                }
              >
                Confirm
              </Button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 3. Contract terms — the agreed commercial terms a supplier can breach
// ---------------------------------------------------------------------------

const EMPTY_TERM = {
  supplier: "",
  country: "",
  product_group: "",
  station_like: "",
  expected_discount_eur_l: "",
  max_net_eur_l: "",
};

function ContractTermsPanel({
  canWrite,
  onRefusal,
  clearRefusal,
}: {
  canWrite: boolean;
  onRefusal: (e: unknown) => void;
  clearRefusal: () => void;
}) {
  const { confirm, dialog } = useConfirm();
  const qc = useQueryClient();
  const [form, setForm] = useState(EMPTY_TERM);

  const terms = useQuery<ContractTerm[]>({
    queryKey: ["transport", "contract-terms"],
    queryFn: async () => (await api.get("/transport/contract-terms")).data,
    retry: false,
  });

  const invalidate = () => {
    clearRefusal();
    qc.invalidateQueries({ queryKey: ["transport", "contract-terms"] });
  };

  const save = useMutation({
    mutationFn: async (body: Record<string, unknown>) =>
      (await api.put("/transport/contract-terms", body)).data as ContractTerm,
    onSuccess: () => {
      setForm(EMPTY_TERM);
      invalidate();
    },
    onError: onRefusal,
  });

  const toggle = useMutation({
    mutationFn: async (row: ContractTerm) =>
      (
        await api.put("/transport/contract-terms", {
          supplier: row.supplier,
          country: row.country,
          product_group: row.product_group,
          station_like: row.station_like,
          expected_discount_eur_l: row.expected_discount_eur_l,
          max_net_eur_l: row.max_net_eur_l,
          active: !row.active,
        })
      ).data as ContractTerm,
    onSuccess: invalidate,
    onError: onRefusal,
  });

  const remove = useMutation({
    mutationFn: async (row: ContractTerm) =>
      (
        await api.delete("/transport/contract-terms", {
          params: {
            supplier: row.supplier,
            country: row.country,
            product_group: row.product_group,
            station_like: row.station_like,
          },
        })
      ).data,
    onSuccess: invalidate,
    onError: onRefusal,
  });

  const hasFigure =
    isDecimalShape(form.expected_discount_eur_l) ||
    isDecimalShape(form.max_net_eur_l);
  const formReady =
    form.supplier.trim() !== "" &&
    form.country.trim().length === 2 &&
    form.product_group.trim() !== "" &&
    hasFigure;

  return (
    <div className="space-y-4">
      {dialog}
      {canWrite && (
        <Card title="Record an agreed term">
          <div className="grid gap-4 sm:grid-cols-3 lg:grid-cols-5">
            <TextInput
              label="Supplier"
              required
              value={form.supplier}
              onChange={(e) => setForm({ ...form, supplier: e.target.value })}
            />
            <TextInput
              label="Country"
              hint="ISO code, e.g. LV"
              required
              maxLength={2}
              value={form.country}
              onChange={(e) => setForm({ ...form, country: e.target.value })}
            />
            <TextInput
              label="Product group"
              required
              value={form.product_group}
              onChange={(e) =>
                setForm({ ...form, product_group: e.target.value })
              }
              placeholder="diesel"
            />
            <TextInput
              label="Station pattern"
              hint="Blank = every station"
              value={form.station_like}
              onChange={(e) =>
                setForm({ ...form, station_like: e.target.value })
              }
            />
            <div />
            <TextInput
              label="Expected discount €/L"
              value={form.expected_discount_eur_l}
              onChange={(e) =>
                setForm({ ...form, expected_discount_eur_l: e.target.value })
              }
              placeholder="0.0000"
            />
            <TextInput
              label="Max net €/L"
              value={form.max_net_eur_l}
              onChange={(e) =>
                setForm({ ...form, max_net_eur_l: e.target.value })
              }
              placeholder="0.0000"
            />
            <div className="flex items-end">
              <Button
                disabled={!formReady}
                loading={save.isPending}
                onClick={() =>
                  save.mutate({
                    supplier: form.supplier.trim(),
                    country: form.country.trim().toUpperCase(),
                    product_group: form.product_group.trim(),
                    station_like: form.station_like.trim(),
                    expected_discount_eur_l:
                      form.expected_discount_eur_l.trim() || null,
                    max_net_eur_l: form.max_net_eur_l.trim() || null,
                    active: true,
                  })
                }
              >
                Save term
              </Button>
            </div>
          </div>
          <p className="mt-3 text-xs text-slate-400">
            Only two kinds of term exist, and at least one figure is required —
            a term with neither cannot be breached, so it cannot be audited.
            Saving the same supplier, country, product group and station pattern
            again updates that term rather than adding a second one.
          </p>
        </Card>
      )}

      <Card title="Agreed terms" padded={false}>
        <QueryState
          query={terms}
          loading={<Skeleton className="m-5 h-24 w-full" />}
          isEmpty={(rows) => rows.length === 0}
          empty={
            <EmptyState
              title="No agreed terms recorded"
              description={
                canWrite
                  ? "Until a term is recorded, an audit has nothing to check against — and a clean audit would mean nothing."
                  : "Nobody has recorded the agreed commercial terms for this workspace yet."
              }
            />
          }
          errorTitle="Couldn’t load the contract terms"
        >
          {(rows) => (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <caption className="sr-only">
                  Agreed supplier contract terms
                </caption>
                <thead>
                  <tr className="text-left text-xs text-slate-400">
                    <th className="px-5 py-2">Supplier</th>
                    <th className="px-5 py-2">Country</th>
                    <th className="px-5 py-2">Product</th>
                    <th className="px-5 py-2">Stations</th>
                    <th className="px-5 py-2 text-right">
                      Expected discount €/L
                    </th>
                    <th className="px-5 py-2 text-right">Max net €/L</th>
                    <th className="px-5 py-2">State</th>
                    {canWrite && <th className="px-5 py-2"></th>}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((t) => (
                    <tr key={t.id} className="border-t border-slate-100">
                      <td className="px-5 py-2">{t.supplier}</td>
                      <td className="px-5 py-2 font-mono text-xs">
                        {t.country}
                      </td>
                      <td className="px-5 py-2 text-xs">{t.product_group}</td>
                      <td className="px-5 py-2 text-xs text-slate-500">
                        {t.station_like || "every station"}
                      </td>
                      <td className="px-5 py-2 text-right tabular-nums">
                        {t.expected_discount_eur_l ?? "—"}
                      </td>
                      <td className="px-5 py-2 text-right tabular-nums">
                        {t.max_net_eur_l ?? "—"}
                      </td>
                      <td className="px-5 py-2">
                        <Badge tone={t.active ? "success" : "neutral"}>
                          {t.active ? "Active" : "Inactive"}
                        </Badge>
                      </td>
                      {canWrite && (
                        <td className="px-5 py-2 text-right">
                          <div className="flex justify-end gap-1">
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => toggle.mutate(t)}
                            >
                              {t.active ? "Deactivate" : "Reactivate"}
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={async () => { if (await confirm({ title: "Delete this agreed term?", body: "Deactivate keeps the history; delete removes the term outright.", confirmLabel: "Delete" })) remove.mutate(t); }}
                            >
                              Delete
                            </Button>
                          </div>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </QueryState>
        <p className="border-t border-slate-100 px-5 py-3 text-xs text-slate-400">
          Deactivate a contract that simply lapsed — a past period can then
          still be audited against the terms that applied to it. Delete only a
          term that was mis-keyed.
        </p>
      </Card>
    </div>
  );
}
