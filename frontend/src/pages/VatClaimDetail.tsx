import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
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
  Select,
  Skeleton,
  StatusBadge,
  TextInput,
} from "../components/ui";
import { api, apiError, apiErrorCode, downloadFile } from "../lib/api";
import { decimalMoney, shortDate } from "../lib/format";
import { hasVatPerm } from "../lib/roles";
import { claimableRefs, isSyntheticRef, stageLadder } from "../lib/transportClaims";
import { useModules } from "../lib/useModules";
import type {
  FuelTransactionList,
  VatChecklistItem,
  VatClaim,
  VatClaimLine,
  VatStage,
  VatStatusCodes,
  VatSubmitInvoice,
  VatWaiver,
} from "../lib/types";

/**
 * A single VAT refund claim — the filing workspace (WO-78).
 *
 * Reads the WO-76 lifecycle routes (`/transport/claims/{id}`, `/lines`,
 * `/checklist`, `/stage`) and drives the WO-76/WO-77 actions (build lines,
 * submit, withdraw, set status code) plus the two WO-77 filing artifacts
 * (`/workbook`, `/evidence`).
 *
 * THE SUBMIT DIALOG IS THE CENTREPIECE. `POST .../submit` runs the D5 gate chain
 * and every refusal is a stable `code` the backend raises from the service. This
 * page turns each one into a sentence that says what is wrong and what to do
 * next (`lib/transportClaims.ts::claimRefusal`) — the raw slug never reaches the
 * screen, and the server's own `detail` (which names the offending suppliers or
 * invoice refs) rides underneath as the specifics.
 *
 * WHAT THE UI DELIBERATELY DOES NOT DO:
 * - It computes NO total. The header figures are the claim's own frozen
 *   `vat_eur`/`vat_local`; the lines table shows each line's own amounts
 *   (master-context §4.10 — the server recomputes every total, never the client).
 * - It does NOT pre-empt a gate. The checklist is ADVISORY (§4.19): a failing
 *   item is shown, and Submit stays enabled — the hard gate is the server's, and
 *   a second gate in the UI would only drift from it.
 * - It invents no status LABEL vocabulary. This codebase deliberately carries
 *   none (WO-77 decision 5), so the ladder renders the service's own codes.
 *
 * THE INVOICE SET IS STILL OPERATOR-SUPPLIED — `submit_claim` keys its locks on
 * `(supplier, invoice_ref, fuel_transaction_id)` tuples the caller passes, and
 * deriving that list server-side from the frozen lines is explicitly "a future
 * slice of G2.6" in `lock.py`'s own docstring. What WO-79 changed is HOW the
 * operator supplies it: `GET /transport/fuel-transactions` now enumerates the
 * claim's own (entity × period × refund country) transactions, so the dialog is a
 * PICK-LIST — supplier and fuel-transaction id come straight off the selected
 * row and are never typed. The invoice reference stays editable because that
 * column is nullable by design ("often unresolved AT INGESTION time", the model's
 * docstring) while the submit schema requires a non-empty one.
 *
 * The typed path survives verbatim ("Add a row manually"), and it is what the
 * dialog falls back to when the period has no transactions or the fuel read
 * fails — pre-seeded, exactly as WO-78 did, from the non-synthetic references on
 * the materialized lines.
 *
 * WHAT IS STILL NOT SHOWN, AND WHY (WO-78 deviation 1, deliberately left open):
 * the claim-line table carries NO supplier column. `VatClaimLine.invoice_ref` is
 * the RESOLVED AP invoice number (or "UNMATCHED"), while a fuel transaction's is
 * the RAW statement note — `invoice_match` exists precisely because those two
 * strings differ. `FuelTransaction.invoice_id` (the one column that would tie
 * them together) is never populated by any service today, and an "UNMATCHED" line
 * aggregates every unresolved supplier into one row. There is therefore no
 * unambiguous link on the wire, and a guessed supplier on a filing surface is
 * worse than an absent one. The honest fix is a backend one (see
 * `docs/plan/plan-a/wo/WO-79-fuel-read-surface.md`, Deviations).
 */

const SYNTHETIC_HINT =
  "This reference is a placeholder, not a real invoice — resolve it before filing.";

const BLANK_ROW: VatSubmitInvoice = { supplier: "", invoice_ref: "", fuel_transaction_id: "" };

export default function VatClaimDetailPage() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const { user } = useAuth();
  const modules = useModules();
  // Cosmetic mirror of the API's own gating (master-context §6) — the server
  // remains the control, and any 403 it returns still renders below.
  const canWrite = hasVatPerm(user, "vat.write");
  const canSubmit = hasVatPerm(user, "vat.submit");

  const [refusal, setRefusal] = useState<{ code: string | null; detail: string } | null>(null);
  const [submitOpen, setSubmitOpen] = useState(false);
  // The pick-list selection: fuel-transaction ids, plus the (editable) invoice
  // reference for each. `refs` starts from the transaction's own raw
  // `invoice_ref`, which is nullable by design — hence editable, not read-only.
  const [picked, setPicked] = useState<string[]>([]);
  const [refs, setRefs] = useState<Record<string, string>>({});
  // The typed path (WO-78), kept. `null` = "not decided yet": the fallback seed
  // is DERIVED at render from the fuel query's outcome rather than pushed in by
  // an effect, so the dialog has no async state race.
  const [rows, setRows] = useState<VatSubmitInvoice[] | null>(null);
  const [waiverSupplier, setWaiverSupplier] = useState("");
  const [codeOpen, setCodeOpen] = useState(false);
  const [decisionOpen, setDecisionOpen] = useState(false);
  const [decisionOutcome, setDecisionOutcome] = useState("approved");
  const [decisionRefs, setDecisionRefs] = useState<string[]>([]);
  const [codeForm, setCodeForm] = useState({ code: "", action_deadline: "" });

  const enabled = modules.isEnabled("transport");

  const claim = useQuery<VatClaim>({
    queryKey: ["transport", "claim", id],
    queryFn: async () => (await api.get(`/transport/claims/${id}`)).data,
    enabled: enabled && !!id,
  });
  const lines = useQuery<VatClaimLine[]>({
    queryKey: ["transport", "claim", id, "lines"],
    queryFn: async () => (await api.get(`/transport/claims/${id}/lines`)).data,
    enabled: enabled && !!id,
  });
  const checklist = useQuery<VatChecklistItem[]>({
    queryKey: ["transport", "claim", id, "checklist"],
    queryFn: async () => (await api.get(`/transport/claims/${id}/checklist`)).data,
    enabled: enabled && !!id,
  });
  // The derived pre-filing stage is only meaningful on a DRAFT claim — the
  // service refuses `claim_not_draft` otherwise, which is "not applicable" here,
  // never an error worth a banner. Hence the `enabled` guard and `retry: false`.
  const isDraft = claim.data?.status === "draft";
  const stage = useQuery<VatStage>({
    queryKey: ["transport", "claim", id, "stage"],
    queryFn: async () => (await api.get(`/transport/claims/${id}/stage`)).data,
    enabled: enabled && !!id && isDraft,
    retry: false,
  });
  // The period's fuel transactions — WO-79's read surface, and the source of
  // every value in the pick-list. Scoped to the CLAIM'S OWN grain (entity ×
  // reference period × refund country), which is exactly the scope
  // `claim_lines.build_claim_lines` materializes the lines from, so the dialog
  // cannot offer a transaction the claim provably excludes. Fetched only while
  // the dialog is open: a read-only viewer never triggers it.
  const fuel = useQuery<FuelTransactionList>({
    queryKey: [
      "transport",
      "fuel-transactions",
      claim.data?.entity_id,
      claim.data?.ref_period,
      claim.data?.refund_country,
    ],
    queryFn: async () =>
      (
        await api.get("/transport/fuel-transactions", {
          params: {
            entity_id: claim.data?.entity_id,
            period: claim.data?.ref_period,
            country: claim.data?.refund_country,
            page_size: 500,
          },
        })
      ).data,
    enabled: enabled && submitOpen && !!claim.data,
    retry: false,
  });
  // WO-80 — the claim-scoped receipt waivers (R15). The one WO-77 surface that
  // is claim-scoped rather than org-level, so it belongs here rather than on
  // the configuration workspace: waiving a supplier changes what THIS claim's
  // lines are built from.
  const waivers = useQuery<VatWaiver[]>({
    queryKey: ["transport", "claim", id, "waivers"],
    queryFn: async () => (await api.get(`/transport/claims/${id}/waivers`)).data,
    enabled: enabled && !!id,
  });
  const statusCodes = useQuery<VatStatusCodes>({
    queryKey: ["transport", "status-codes"],
    queryFn: async () => (await api.get("/transport/status-codes")).data,
    enabled,
    retry: false,
  });

  const refresh = () => {
    setRefusal(null);
    qc.invalidateQueries({ queryKey: ["transport"] });
  };
  const onRefusal = (e: unknown) => setRefusal({ code: apiErrorCode(e), detail: apiError(e) });

  const buildLines = useMutation({
    mutationFn: async () => (await api.post(`/transport/claims/${id}/lines`)).data,
    onSuccess: refresh,
    onError: onRefusal,
  });
  // The typed rows actually in play. `rows === null` means the operator has not
  // touched the manual section yet, so it falls back to WO-78's pre-seed — but
  // only when the pick-list has nothing to offer (no transactions, or the fuel
  // read failed); with a working pick-list the manual section starts empty.
  const fuelItems = fuel.data?.items ?? [];
  const pickListUnavailable = fuel.isError || (!fuel.isLoading && fuelItems.length === 0);
  const seededRows = useMemo<VatSubmitInvoice[]>(() => {
    const refsOnLines = claimableRefs(lines.data ?? []);
    return refsOnLines.map((ref) => ({
      supplier: "",
      invoice_ref: ref,
      fuel_transaction_id: "",
    }));
  }, [lines.data]);
  const manualRows: VatSubmitInvoice[] =
    rows ?? (pickListUnavailable ? (seededRows.length > 0 ? seededRows : [BLANK_ROW]) : []);

  /** The exact `invoices` array posted to `POST .../submit`.
   *
   * Picked transactions contribute their OWN supplier and id — never retyped —
   * with the (editable) invoice reference. Manual rows contribute what was
   * typed. A row left entirely blank is dropped: it is a row the operator never
   * filled in, not a gate being pre-empted. Nothing else is filtered — an
   * invalid set still goes to the server, which is the control (§6/§4.19).
   */
  const submitInvoices = (): VatSubmitInvoice[] => {
    const fromPicks = fuelItems
      .filter((t) => picked.includes(t.id))
      .map((t) => ({
        supplier: t.supplier,
        invoice_ref: (refs[t.id] ?? t.invoice_ref ?? "").trim(),
        fuel_transaction_id: t.id,
      }));
    const fromTyped = manualRows
      .map((r) => ({
        supplier: r.supplier.trim(),
        invoice_ref: r.invoice_ref.trim(),
        fuel_transaction_id: r.fuel_transaction_id.trim(),
      }))
      .filter((r) => r.supplier || r.invoice_ref || r.fuel_transaction_id);
    return [...fromPicks, ...fromTyped];
  };

  const submit = useMutation({
    mutationFn: async (override: boolean) =>
      (
        await api.post(`/transport/claims/${id}/submit`, {
          invoices: submitInvoices(),
          override_minimum: override,
        })
      ).data,
    onSuccess: () => {
      setSubmitOpen(false);
      refresh();
    },
    onError: (e) => {
      // Close the dialog so the refusal — and, for `below_minimum`, its override
      // action — is what the operator sees. The typed rows stay in state, so
      // re-opening restores them; a refusal never costs re-entry.
      setSubmitOpen(false);
      onRefusal(e);
    },
  });
  const withdraw = useMutation({
    mutationFn: async () => (await api.post(`/transport/claims/${id}/withdraw`)).data,
    onSuccess: refresh,
    onError: onRefusal,
  });
  // WO-L (§13): the member state's answer. Partial rejection names the
  // rejected invoice refs; the server shrinks the frozen base and recomputes
  // the fee at the FROZEN rate — the SPA only reports, never re-derives.
  const recordDecision = useMutation({
    mutationFn: async () =>
      (
        await api.post(`/transport/claims/${id}/decision`, {
          outcome: decisionOutcome,
          ...(decisionOutcome === "partial" ? { rejected_refs: decisionRefs } : {}),
        })
      ).data,
    onSuccess: () => {
      setDecisionOpen(false);
      setDecisionRefs([]);
      refresh();
    },
    onError: (e) => {
      setDecisionOpen(false);
      onRefusal(e);
    },
  });
  const addWaiver = useMutation({
    mutationFn: async () =>
      (await api.post(`/transport/claims/${id}/waivers`, { supplier: waiverSupplier.trim() })).data,
    onSuccess: () => {
      setWaiverSupplier("");
      refresh();
    },
    onError: onRefusal,
  });
  const removeWaiver = useMutation({
    mutationFn: async (supplier: string) =>
      (await api.delete(`/transport/claims/${id}/waivers/${encodeURIComponent(supplier)}`)).data,
    onSuccess: refresh,
    onError: onRefusal,
  });
  const setStatusCode = useMutation({
    mutationFn: async () =>
      (
        await api.post(`/transport/claims/${id}/status-code`, {
          code: codeForm.code,
          action_deadline: codeForm.action_deadline || null,
        })
      ).data,
    onSuccess: () => {
      setCodeOpen(false);
      refresh();
    },
    onError: onRefusal,
  });

  const download = async (kind: "workbook" | "evidence") => {
    setRefusal(null);
    const ext = kind === "workbook" ? "xlsx" : "zip";
    try {
      await downloadFile(`/transport/claims/${id}/${kind}`, `claim-${id}-${kind}.${ext}`);
    } catch (e) {
      onRefusal(e);
    }
  };

  const ladder = useMemo(
    () =>
      claim.data
        ? stageLadder(statusCodes.data, claim.data, stage.data?.stage ?? null)
        : [],
    [statusCodes.data, claim.data, stage.data],
  );

  const openSubmit = () => {
    // Reset to "nothing chosen, nothing typed" — the pick-list (WO-79) is the
    // primary path and the manual section derives its fallback seed at render.
    setPicked([]);
    setRefs({});
    setRows(null);
    setRefusal(null);
    setSubmitOpen(true);
  };

  const togglePick = (txnId: string, fallbackRef: string | null) => {
    setPicked((prev) =>
      prev.includes(txnId) ? prev.filter((x) => x !== txnId) : [...prev, txnId],
    );
    setRefs((prev) => (txnId in prev ? prev : { ...prev, [txnId]: fallbackRef ?? "" }));
  };

  if (modules.isLoading) return <Skeleton className="h-24 w-full" />;
  if (!enabled) {
    return (
      <div className="space-y-6">
        <PageHeader title="VAT refund claim" />
        <ModuleInactive name={"Transport & VAT refunds"} />
      </div>
    );
  }

  const c = claim.data;
  const belowMinimum = refusal?.code === "below_minimum";

  return (
    <div className="space-y-6">
      <PageHeader
        breadcrumbs={[{ label: "VAT refund claims", to: "/vat-claims" }, { label: "Claim" }]}
        title={
          c ? `${c.refund_country} · ${c.ref_period}` : "VAT refund claim"
        }
        description="One claim per legal entity, refunding country and reference period. Every amount below is the figure the service stored — nothing on this page is recalculated."
        meta={
          c && (
            <>
              <StatusBadge status={c.status} />
              {c.status_code && <Badge tone="info">Code {c.status_code}</Badge>}
              {c.action_deadline && (
                <Badge tone="warning">Action by {shortDate(c.action_deadline)}</Badge>
              )}
            </>
          )
        }
        actions={
          <>
            <Button variant="secondary" onClick={() => download("workbook")}>
              Claim workbook
            </Button>
            <Button variant="secondary" onClick={() => download("evidence")}>
              Evidence pack
            </Button>
            {canWrite && isDraft && (
              <Button
                variant="secondary"
                loading={buildLines.isPending}
                onClick={() => buildLines.mutate()}
              >
                Build lines
              </Button>
            )}
            {canSubmit && isDraft && <Button onClick={openSubmit}>Submit claim</Button>}
            {canSubmit && !isDraft && (
              <>
                {claim.data?.status === "submitted" && (
                  <Button onClick={() => setDecisionOpen(true)}>Record decision</Button>
                )}
                <Button variant="secondary" onClick={() => setCodeOpen(true)}>
                  Set status code
                </Button>
                <Button
                  variant="danger"
                  loading={withdraw.isPending}
                  onClick={() => withdraw.mutate()}
                >
                  Withdraw
                </Button>
              </>
            )}
          </>
        }
      />

      {refusal && (
        <RefusalNotice
          code={refusal.code}
          detail={refusal.detail}
          onDismiss={() => setRefusal(null)}
          action={
            belowMinimum && canSubmit ? (
              <Button
                variant="secondary"
                size="sm"
                loading={submit.isPending}
                onClick={() => submit.mutate(true)}
              >
                File it anyway and record the override
              </Button>
            ) : undefined
          }
        />
      )}

      <QueryState
        query={claim}
        loading={<Skeleton className="h-32 w-full" />}
        errorTitle="Couldn’t load this claim"
      >
        {(data) => (
          <div className="grid gap-6 lg:grid-cols-3">
            <Card title="Claim" className="lg:col-span-1">
              <dl className="space-y-2 text-sm">
                <Row label="Legal entity" value={<span className="font-mono text-xs">{data.entity_id}</span>} />
                <Row label="Refund country" value={data.refund_country} />
                <Row label="Reference period" value={data.ref_period} />
                <Row label="Status" value={<StatusBadge status={data.status} />} />
                <Row label="Submitted" value={shortDate(data.submitted_date)} />
                <Row label="Decision" value={shortDate(data.decision_date)} />
                <Row label="Paid" value={shortDate(data.paid_date)} />
                {data.status_note && (
                  <div className="border-t border-slate-100 pt-2">
                    <dt className="text-xs text-slate-400">Note recorded by the service</dt>
                    <dd className="mt-1 text-xs text-slate-600">{data.status_note}</dd>
                  </div>
                )}
              </dl>
            </Card>

            <Card title="Totals as filed" className="lg:col-span-2">
              <div className="grid gap-4 sm:grid-cols-3">
                <Figure
                  label="Frozen VAT (EUR)"
                  value={decimalMoney(data.vat_eur)}
                  note={data.vat_eur === null ? "Frozen when the claim is filed" : "As frozen at submission"}
                />
                <Figure
                  label={`Frozen VAT (${data.currency ?? "local"})`}
                  value={decimalMoney(data.vat_local, data.currency)}
                  note="The refunding country's own currency"
                />
                <Figure
                  label="Service fee"
                  value={decimalMoney(data.fee_eur)}
                  note="Recorded on the claim"
                />
                <Figure
                  label="Refund received"
                  value={decimalMoney(data.paid_amount)}
                  note="As recorded when the refund landed"
                />
              </div>
              <p className="mt-4 text-xs text-slate-400">
                These are the figures the service stored. This page performs no arithmetic on
                them — amounts cross the wire as exact decimal values and are shown as such.
              </p>
            </Card>

            <Card title="Stage" className="lg:col-span-3">
              {ladder.length === 0 ? (
                <p className="text-sm text-slate-500">
                  The stage vocabulary isn’t available for this workspace.
                </p>
              ) : (
                <>
                  <ol className="flex flex-wrap gap-2" aria-label="Claim stage">
                    {ladder.map((step) => (
                      <li key={step.code}>
                        <span
                          aria-current={step.state === "current" ? "step" : undefined}
                          className={
                            step.state === "current"
                              ? "inline-flex items-center rounded-full bg-brand-500 px-2.5 py-0.5 text-xs font-semibold text-white"
                              : step.state === "done"
                                ? "inline-flex items-center rounded-full bg-brand-50 px-2.5 py-0.5 text-xs font-medium text-brand-700"
                                : "inline-flex items-center rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-slate-400"
                          }
                        >
                          {step.code}
                        </span>
                      </li>
                    ))}
                  </ol>
                  <p className="mt-3 text-xs text-slate-400">
                    {isDraft
                      ? "Codes 1A–1E are derived by the service from the checklist and can’t be set by hand. Filing stamps code 2."
                      : "Codes from 2A onward are the post-filing workflow — set them as the refunding authority responds."}
                  </p>
                </>
              )}
            </Card>

            <Card title="Submission checklist" className="lg:col-span-1">
              <p className="mb-3 text-xs text-slate-400">
                Advisory. A failing item never blocks the Submit button — the service runs its own
                gates when you file.
              </p>
              <QueryState
                query={checklist}
                loading={<Skeleton className="h-20 w-full" />}
                isEmpty={(items) => items.length === 0}
                empty={<EmptyState title="No checklist rules are active" />}
                errorTitle="Couldn’t load the checklist"
              >
                {(items) => (
                  <ul className="space-y-2 text-sm">
                    {items.map((item) => (
                      <li key={item.key} className="flex items-start gap-2">
                        <Badge tone={item.ok ? "success" : "warning"}>
                          {item.ok ? "OK" : "Check"}
                        </Badge>
                        <span className="min-w-0">
                          <span className="font-medium text-slate-700">{item.label}</span>
                          <span className="ml-1 text-xs text-slate-400">({item.scope})</span>
                          {!item.ok && item.reason && (
                            <span className="block text-xs text-slate-500">{item.reason}</span>
                          )}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </QueryState>
            </Card>

            <Card title="Claim lines" padded={false} className="lg:col-span-2">
              <QueryState
                query={lines}
                loading={<Skeleton className="m-5 h-24 w-full" />}
                isEmpty={(rowsIn) => rowsIn.length === 0}
                empty={
                  <EmptyState
                    title="No claim lines yet"
                    description={
                      canWrite && isDraft
                        ? "Build the lines to group this period's fuel transactions by invoice and product."
                        : "Lines are materialized from the period's fuel transactions before filing."
                    }
                  />
                }
                errorTitle="Couldn’t load the claim lines"
              >
                {(rowsIn) => (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <caption className="sr-only">Materialized claim lines</caption>
                      <thead>
                        <tr className="text-left text-xs text-slate-400">
                          <th className="px-5 py-2">Invoice ref</th>
                          <th className="px-5 py-2">Goods code</th>
                          <th className="px-5 py-2">Product</th>
                          <th className="px-5 py-2 text-right">Net</th>
                          <th className="px-5 py-2 text-right">VAT</th>
                          <th className="px-5 py-2">Frozen</th>
                        </tr>
                      </thead>
                      <tbody>
                        {rowsIn.map((line) => {
                          const synthetic = isSyntheticRef(line.invoice_ref, line.vat_id);
                          return (
                            <tr key={line.id} className="border-t border-slate-100">
                              <td className="px-5 py-2 font-mono text-xs">
                                {line.invoice_ref}
                                {synthetic && (
                                  <span className="ml-2 inline-block">
                                    <Badge tone="warning">unresolved</Badge>
                                  </span>
                                )}
                                {synthetic && (
                                  <span className="block text-xs font-sans text-slate-400">
                                    {SYNTHETIC_HINT}
                                  </span>
                                )}
                                {line.unmatched_suppliers && line.unmatched_suppliers.length > 0 && (
                                  <span className="block text-xs font-sans text-amber-600">
                                    suppliers to chase: {line.unmatched_suppliers.join(", ")}
                                  </span>
                                )}
                                {line.rejected_at && (
                                  <span className="ml-2 inline-block">
                                    <Badge tone="danger">rejected</Badge>
                                  </span>
                                )}
                              </td>
                              <td className="px-5 py-2">{line.goods_code ?? "—"}</td>
                              <td className="px-5 py-2 text-slate-500">
                                {line.product_group ?? "—"}
                              </td>
                              <td className="px-5 py-2 text-right tabular-nums">
                                {decimalMoney(line.net_eur)}
                              </td>
                              <td className="px-5 py-2 text-right tabular-nums">
                                {decimalMoney(line.vat_eur)}
                              </td>
                              <td className="px-5 py-2 text-xs text-slate-500">
                                {line.frozen_at ? shortDate(line.frozen_at) : "draft"}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </QueryState>
            </Card>

            {/* R15 — the claim-scoped receipt waivers (WO-80). A waiver says a
                supplier genuinely never invoiced for this claim's country, so
                the service leaves its transactions out when the lines are
                built. It is NOT the receipt-control grid's mute (that one is a
                worklist row on the configuration page and changes nothing);
                this one changes what the claim contains, which is why it is
                only offered while the claim is still a draft. */}
            <Card title="Suppliers waived on this claim" className="lg:col-span-3">
              <p className="mb-3 text-xs text-slate-400">
                Waive a supplier only when it genuinely issued no invoice for this country and
                period — its transactions are then left out when the lines are built. If a
                transaction has simply not been tied to its invoice yet, map its statement
                reference to the right invoice instead; waiving would drop VAT you can reclaim.
              </p>
              <QueryState
                query={waivers}
                loading={<Skeleton className="h-16 w-full" />}
                isEmpty={(rowsIn) => rowsIn.length === 0}
                empty={<EmptyState title="No supplier is waived on this claim" />}
                errorTitle="Couldn’t load the waivers"
              >
                {(rowsIn) => (
                  <ul className="space-y-2 text-sm">
                    {rowsIn.map((w) => (
                      <li key={w.id} className="flex items-center justify-between gap-3">
                        <span className="font-medium text-slate-700">{w.supplier}</span>
                        {canWrite && isDraft && (
                          <Button
                            size="sm"
                            variant="secondary"
                            loading={removeWaiver.isPending && removeWaiver.variables === w.supplier}
                            onClick={() => removeWaiver.mutate(w.supplier)}
                          >
                            Un-waive
                          </Button>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </QueryState>
              {canWrite && isDraft && (
                <div className="mt-4 flex flex-wrap items-end gap-3">
                  <TextInput
                    label="Waive a supplier"
                    hint="The supplier code exactly as it appears on the transactions."
                    value={waiverSupplier}
                    onChange={(e) => setWaiverSupplier(e.target.value)}
                    placeholder="Supplier code"
                  />
                  <Button
                    variant="secondary"
                    loading={addWaiver.isPending}
                    disabled={waiverSupplier.trim() === ""}
                    onClick={() => addWaiver.mutate()}
                  >
                    Waive
                  </Button>
                </div>
              )}
            </Card>
          </div>
        )}
      </QueryState>

      <Modal
        open={decisionOpen}
        onClose={() => setDecisionOpen(false)}
        title="Record the member state's decision"
        description="Approved keeps every figure; rejected keeps them too (the frozen base stands). A partial rejection names the rejected invoices — the claim's base and fee shrink to what survived, at the fee rate frozen at filing."
        footer={
          <>
            <Button variant="secondary" onClick={() => setDecisionOpen(false)}>
              Cancel
            </Button>
            <Button
              loading={recordDecision.isPending}
              disabled={decisionOutcome === "partial" && decisionRefs.length === 0}
              onClick={() => recordDecision.mutate()}
            >
              Record
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <label className="block text-sm">
            <span className="mb-1 block text-xs text-slate-500">Outcome</span>
            <select
              className="rounded-lg border border-slate-300 px-2 py-1 text-sm"
              value={decisionOutcome}
              onChange={(e) => setDecisionOutcome(e.target.value)}
              aria-label="Decision outcome"
            >
              <option value="approved">Approved in full</option>
              <option value="rejected">Rejected in full</option>
              <option value="partial">Partially rejected</option>
            </select>
          </label>
          {decisionOutcome === "partial" && (
            <div>
              <p className="mb-1 text-xs text-slate-500">
                Rejected invoices (the rest of the claim stands):
              </p>
              <div className="max-h-40 space-y-1 overflow-y-auto">
                {[...new Set((lines.data ?? []).filter((l) => l.frozen_at).map((l) => l.invoice_ref))].map(
                  (ref) => (
                    <label key={ref} className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={decisionRefs.includes(ref)}
                        onChange={(e) =>
                          setDecisionRefs((prev) =>
                            e.target.checked ? [...prev, ref] : prev.filter((r) => r !== ref),
                          )
                        }
                      />
                      <span className="font-mono text-xs">{ref}</span>
                    </label>
                  ),
                )}
              </div>
            </div>
          )}
        </div>
      </Modal>

      <Modal
        open={submitOpen}
        onClose={() => setSubmitOpen(false)}
        title="File this claim"
        description="List every invoice this claim covers. Filing freezes the lines and locks each invoice so it can’t be claimed twice."
        size="lg"
        footer={
          <>
            <Button variant="secondary" onClick={() => setSubmitOpen(false)}>
              Cancel
            </Button>
            <Button loading={submit.isPending} onClick={() => submit.mutate(false)}>
              File claim
            </Button>
          </>
        }
      >
        <div className="space-y-5">
          <section>
            <h3 className="text-sm font-semibold text-slate-700">
              This period’s fuel transactions
            </h3>
            <p className="mt-1 text-xs text-slate-400">
              Tick the transactions this claim covers. The supplier and the transaction id are
              taken from the row itself — the invoice reference stays editable because a statement
              line does not always carry one.
            </p>
            <QueryState
              query={fuel}
              loading={<Skeleton className="mt-3 h-24 w-full" />}
              isEmpty={(list) => list.items.length === 0}
              empty={
                <EmptyState
                  title="No fuel transactions for this period"
                  description="Nothing has been captured for this entity, period and refund country yet. Add the invoices by hand below."
                />
              }
              errorTitle="Couldn’t load this period’s fuel transactions"
            >
              {(list) => (
                <>
                  <div className="mt-3 max-h-72 overflow-auto rounded border border-slate-100">
                    <table className="w-full text-sm">
                      <caption className="sr-only">
                        Fuel transactions available for this claim
                      </caption>
                      <thead className="sticky top-0 bg-white">
                        <tr className="text-left text-xs text-slate-400">
                          <th className="px-3 py-2">Use</th>
                          <th className="px-3 py-2">Supplier</th>
                          <th className="px-3 py-2">Date</th>
                          <th className="px-3 py-2">Station / product</th>
                          <th className="px-3 py-2 text-right">Qty</th>
                          <th className="px-3 py-2 text-right">Net</th>
                          <th className="px-3 py-2 text-right">VAT</th>
                          <th className="px-3 py-2">Invoice reference</th>
                        </tr>
                      </thead>
                      <tbody>
                        {list.items.map((txn) => {
                          const on = picked.includes(txn.id);
                          return (
                            <tr key={txn.id} className="border-t border-slate-100">
                              <td className="px-3 py-2">
                                <input
                                  type="checkbox"
                                  checked={on}
                                  aria-label={`Include ${txn.supplier} ${txn.station} ${txn.txn_date}`}
                                  onChange={() => togglePick(txn.id, txn.invoice_ref)}
                                />
                              </td>
                              <td className="px-3 py-2 font-medium text-slate-700">
                                {txn.supplier}
                              </td>
                              <td className="px-3 py-2 text-xs text-slate-500">
                                {shortDate(txn.txn_date)}
                              </td>
                              <td className="px-3 py-2 text-xs text-slate-500">
                                {txn.station}
                                <span className="block text-slate-400">{txn.product_group}</span>
                              </td>
                              {/* Litres/units — an exact wire string, not money and never
                                  arithmetic'd. */}
                              <td className="px-3 py-2 text-right tabular-nums text-xs text-slate-500">
                                {txn.qty}
                              </td>
                              <td className="px-3 py-2 text-right tabular-nums">
                                {decimalMoney(txn.net_eur)}
                              </td>
                              <td className="px-3 py-2 text-right tabular-nums">
                                {decimalMoney(txn.vat_eur)}
                              </td>
                              <td className="px-3 py-2">
                                <TextInput
                                  label=""
                                  value={refs[txn.id] ?? txn.invoice_ref ?? ""}
                                  disabled={!on}
                                  aria-label={`Invoice reference for ${txn.supplier} ${txn.txn_date}`}
                                  placeholder="INV-…"
                                  onChange={(e) =>
                                    setRefs({ ...refs, [txn.id]: e.target.value })
                                  }
                                />
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                  {list.total > list.items.length && (
                    <p className="mt-2 text-xs text-slate-400">
                      Showing {list.items.length} of {list.total} transactions. Narrow the period
                      or add the remaining invoices by hand.
                    </p>
                  )}
                </>
              )}
            </QueryState>
          </section>

          <section>
            <h3 className="text-sm font-semibold text-slate-700">Add an invoice by hand</h3>
            <p className="mt-1 text-xs text-slate-400">
              For anything the list above can’t cover. The supplier and the fuel transaction each
              invoice settles identify the lock the service takes.
            </p>
            <div className="mt-3 space-y-3">
              {manualRows.map((row, i) => (
                <div key={i} className="grid gap-3 sm:grid-cols-3">
                  <TextInput
                    label={i === 0 ? "Supplier" : ""}
                    value={row.supplier}
                    onChange={(e) =>
                      setRows(
                        manualRows.map((r, j) =>
                          j === i ? { ...r, supplier: e.target.value } : r,
                        ),
                      )
                    }
                    placeholder="Supplier"
                  />
                  <TextInput
                    label={i === 0 ? "Invoice reference" : ""}
                    value={row.invoice_ref}
                    onChange={(e) =>
                      setRows(
                        manualRows.map((r, j) =>
                          j === i ? { ...r, invoice_ref: e.target.value } : r,
                        ),
                      )
                    }
                    placeholder="INV-…"
                  />
                  <TextInput
                    label={i === 0 ? "Fuel transaction id" : ""}
                    value={row.fuel_transaction_id}
                    onChange={(e) =>
                      setRows(
                        manualRows.map((r, j) =>
                          j === i ? { ...r, fuel_transaction_id: e.target.value } : r,
                        ),
                      )
                    }
                    placeholder="Transaction id"
                  />
                </div>
              ))}
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setRows([...manualRows, BLANK_ROW])}
              >
                Add a row manually
              </Button>
            </div>
          </section>
        </div>
      </Modal>

      <Modal
        open={codeOpen}
        onClose={() => setCodeOpen(false)}
        title="Set the workflow code"
        description="Records where this filed claim stands with the refunding authority. Codes 1A–1E are derived by the service and can’t be set here."
        footer={
          <>
            <Button variant="secondary" onClick={() => setCodeOpen(false)}>
              Cancel
            </Button>
            <Button
              loading={setStatusCode.isPending}
              disabled={!codeForm.code}
              onClick={() => setStatusCode.mutate()}
            >
              Save code
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <Select
            label="Workflow code"
            required
            value={codeForm.code}
            onChange={(e) => setCodeForm({ ...codeForm, code: e.target.value })}
          >
            <option value="">Choose…</option>
            {(statusCodes.data?.manual ?? []).map((code) => (
              <option key={code} value={code}>
                {code}
              </option>
            ))}
          </Select>
          <TextInput
            label="Action deadline"
            hint="Optional. A reminder only — it never rejects or forfeits a claim."
            type="date"
            value={codeForm.action_deadline}
            onChange={(e) => setCodeForm({ ...codeForm, action_deadline: e.target.value })}
          />
        </div>
      </Modal>

      <p className="text-xs text-slate-400">
        <Link to="/vat-claims" className="underline">
          Back to all claims
        </Link>
      </p>
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="text-xs text-slate-400">{label}</dt>
      <dd className="text-sm text-slate-700">{value}</dd>
    </div>
  );
}

function Figure({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div>
      <p className="text-xs text-slate-400">{label}</p>
      <p className="mt-1 text-lg font-semibold tabular-nums text-slate-800">{value}</p>
      <p className="mt-0.5 text-xs text-slate-400">{note}</p>
    </div>
  );
}
