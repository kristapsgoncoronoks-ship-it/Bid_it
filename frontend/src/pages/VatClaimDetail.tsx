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
  VatChecklistItem,
  VatClaim,
  VatClaimLine,
  VatStage,
  VatStatusCodes,
  VatSubmitInvoice,
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
 * THE INVOICE SET IS OPERATOR-SUPPLIED, and that is a recorded limitation of the
 * current wire, not a design choice: `submit_claim` keys its locks on
 * `(supplier, invoice_ref, fuel_transaction_id)` tuples the caller passes, and no
 * route enumerates fuel transactions yet (`api/routes/transport/fuel.py` does not
 * exist — out of scope in both WO-76 and WO-77). The dialog pre-seeds a row per
 * non-synthetic materialized line so the invoice reference is at least filled in.
 */

const SYNTHETIC_HINT =
  "This reference is a placeholder, not a real invoice — resolve it before filing.";

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
  const [rows, setRows] = useState<VatSubmitInvoice[]>([]);
  const [codeOpen, setCodeOpen] = useState(false);
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
  const submit = useMutation({
    mutationFn: async (override: boolean) =>
      (
        await api.post(`/transport/claims/${id}/submit`, {
          invoices: rows.map((r) => ({
            supplier: r.supplier.trim(),
            invoice_ref: r.invoice_ref.trim(),
            fuel_transaction_id: r.fuel_transaction_id.trim(),
          })),
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
    // Pre-seed one row per non-synthetic invoice reference on the materialized
    // lines. Supplier and fuel-transaction id stay empty: no route serves them.
    const refs = claimableRefs(lines.data ?? []);
    setRows(
      refs.length > 0
        ? refs.map((ref) => ({ supplier: "", invoice_ref: ref, fuel_transaction_id: "" }))
        : [{ supplier: "", invoice_ref: "", fuel_transaction_id: "" }],
    );
    setRefusal(null);
    setSubmitOpen(true);
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
          </div>
        )}
      </QueryState>

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
        <div className="space-y-3">
          {rows.map((row, i) => (
            <div key={i} className="grid gap-3 sm:grid-cols-3">
              <TextInput
                label={i === 0 ? "Supplier" : ""}
                value={row.supplier}
                onChange={(e) =>
                  setRows(rows.map((r, j) => (j === i ? { ...r, supplier: e.target.value } : r)))
                }
                placeholder="Supplier"
              />
              <TextInput
                label={i === 0 ? "Invoice reference" : ""}
                value={row.invoice_ref}
                onChange={(e) =>
                  setRows(rows.map((r, j) => (j === i ? { ...r, invoice_ref: e.target.value } : r)))
                }
                placeholder="INV-…"
              />
              <TextInput
                label={i === 0 ? "Fuel transaction id" : ""}
                value={row.fuel_transaction_id}
                onChange={(e) =>
                  setRows(
                    rows.map((r, j) =>
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
            onClick={() =>
              setRows([...rows, { supplier: "", invoice_ref: "", fuel_transaction_id: "" }])
            }
          >
            Add another invoice
          </Button>
          <p className="text-xs text-slate-400">
            Invoice references are pre-filled from the claim’s materialized lines. The supplier and
            the fuel transaction each invoice settles identify the lock the service takes.
          </p>
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
