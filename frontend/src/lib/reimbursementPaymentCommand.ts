import axios from "axios";
import { api, apiErrorCode } from "./api";
import type { ExpenseCommandScope } from "./manualExpenseCommand";
import {
  PAYOUT_READ_TIMEOUT_MS, payoutBatchDetail, payoutCommandDigest, payoutCommandGuard,
  payoutCommandHeaders, payoutDecimal, payoutMinor, selectionTotal,
  type PaymentEvidence, type PayoutBatch, type PayoutBatchDetail, type PayoutReport,
} from "./reimbursementCreateCommand";

interface PaymentPayload {
  version: number;
  expected_total_eur: string;
  report_versions: Record<string, number>;
  reference: string | null;
  method: null;
  override_sod: false;
}
export interface SavedPaymentCommand {
  key: string;
  digest: string;
  batchId: string;
  payload: PaymentPayload;
  expectedMethod: string;
}
export interface PaymentReference {
  batchId: string;
  command: SavedPaymentCommand | null;
  error: string | null;
}
const ID = /^[A-Za-z0-9-]{1,120}$/;
const UNVERIFIED = "The payment record could not be verified. Keep its reference and check it again before another batch action.";

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(UNVERIFIED);
  return value as Record<string, unknown>;
}
function prefix(scope: ExpenseCommandScope): string {
  return `invoiceiq:command:${scope.userId}:${scope.orgId}:/reimbursements/`;
}
function slot(scope: ExpenseCommandScope, batchId: string): string {
  if (!ID.test(batchId)) throw new Error(UNVERIFIED);
  return `${prefix(scope)}${batchId}/payment`;
}
function payload(value: unknown): PaymentPayload {
  const body = object(value);
  const versions = object(body.report_versions);
  const ids = Object.keys(versions).sort();
  if (!Number.isInteger(body.version) || (body.version as number) < 1 ||
      ids.length < 1 || ids.length > 1000 || ids.some((id) => !ID.test(id) || !Number.isInteger(versions[id]) || (versions[id] as number) < 1) ||
      !(body.reference === null || (typeof body.reference === "string" && body.reference.length <= 200)) ||
      body.method !== null || body.override_sod !== false) throw new Error(UNVERIFIED);
  return Object.freeze({ version: body.version as number,
    expected_total_eur: payoutDecimal(payoutMinor(body.expected_total_eur)),
    report_versions: Object.freeze(Object.fromEntries(ids.map((id) => [id, versions[id] as number]))),
    reference: body.reference as string | null, method: null, override_sod: false });
}
function fingerprint(command: Pick<SavedPaymentCommand, "batchId" | "payload" | "expectedMethod">) {
  return { batchId: command.batchId, payload: command.payload, expectedMethod: command.expectedMethod };
}
export function readPaymentCommand(scope: ExpenseCommandScope, batchId: string): SavedPaymentCommand | null {
  const stored = sessionStorage.getItem(slot(scope, batchId));
  if (stored === null) return null;
  try {
    const row = object(JSON.parse(stored));
    if (row.batchId !== batchId || typeof row.key !== "string" || !/^[A-Za-z0-9:_-]{1,120}$/.test(row.key) ||
        typeof row.digest !== "string" || !/^[a-f0-9]{64}$/.test(row.digest) ||
        typeof row.expectedMethod !== "string" || !row.expectedMethod || row.expectedMethod.length > 40) throw new Error(UNVERIFIED);
    return { batchId, key: row.key, digest: row.digest, expectedMethod: row.expectedMethod, payload: payload(row.payload) };
  } catch { throw new Error("The saved payment reference is unreadable. Review this batch with an administrator before another payment action."); }
}
/** References remain reachable after reload or paging away from their batch. */
export function paymentReferences(scope: ExpenseCommandScope): PaymentReference[] {
  const found: PaymentReference[] = [];
  const start = prefix(scope);
  for (let i = 0; i < sessionStorage.length; i++) {
    const key = sessionStorage.key(i);
    if (!key?.startsWith(start) || !key.endsWith("/payment")) continue;
    const batchId = key.slice(start.length, -"/payment".length);
    if (!ID.test(batchId)) throw new Error("A saved payment reference is unreadable. Ask an administrator before another batch action.");
    try { found.push({ batchId, command: readPaymentCommand(scope, batchId), error: null }); }
    catch (error) { found.push({ batchId, command: null, error: error instanceof Error ? error.message : UNVERIFIED }); }
  }
  return found.sort((a, b) => a.batchId.localeCompare(b.batchId));
}
function versionedReports(batch: PayoutBatchDetail): PayoutReport[] {
  for (const report of batch.reports) {
    if (!Number.isInteger(report.version) || (report.version as number) < 1) throw new Error(UNVERIFIED);
  }
  return batch.reports as PayoutReport[];
}
/** Read before confirming; never substitute a changed total/version into the click. */
export async function reviewPaymentBatch(
  scope: ExpenseCommandScope, observed: PayoutBatch, signal: AbortSignal,
): Promise<PayoutBatchDetail> {
  const current = payoutCommandGuard(signal); current();
  if (!ID.test(observed.id)) throw new Error(UNVERIFIED);
  const response = await api.get(`/reimbursements/${observed.id}`, {
    signal, headers: payoutCommandHeaders(scope), timeout: PAYOUT_READ_TIMEOUT_MS,
  });
  current();
  const batch = payoutBatchDetail(response.data);
  if (batch.id !== observed.id || batch.status !== "open" || batch.version !== observed.version ||
      batch.report_count !== observed.report_count || batch.report_count < 1 ||
      batch.method !== observed.method || payoutMinor(batch.total_eur) !== payoutMinor(observed.total_eur) ||
      batch.reports.some((report) => !["approved", "marked_for_reimbursement"].includes(report.status)) ||
      selectionTotal(versionedReports(batch)) !== payoutDecimal(payoutMinor(batch.total_eur))) {
    throw new Error("This batch or its reports changed. Refresh its status and review the current amount before recording payment.");
  }
  return batch;
}
/** Copy the reviewed facts synchronously; hash only the frozen intention. */
export async function preparePaymentCommand(
  scope: ExpenseCommandScope, batch: PayoutBatchDetail, reference: string | null, signal: AbortSignal,
): Promise<SavedPaymentCommand> {
  const current = payoutCommandGuard(signal); current();
  if (readPaymentCommand(scope, batch.id)) throw new Error("Check this batch’s saved payment request before recording another payment.");
  const reports = versionedReports(batch);
  const body = payload({ version: batch.version, expected_total_eur: selectionTotal(reports),
    report_versions: Object.fromEntries(reports.map((report) => [report.id, report.version])),
    reference, method: null, override_sod: false });
  const frozen = { batchId: batch.id, payload: body, expectedMethod: batch.method };
  const digest = await payoutCommandDigest(fingerprint(frozen));
  current();
  if (readPaymentCommand(scope, batch.id)) throw new Error("Check this batch’s saved payment request before recording another payment.");
  const saved = { ...frozen, key: crypto.randomUUID(), digest };
  sessionStorage.setItem(slot(scope, batch.id), JSON.stringify(saved));
  return saved;
}
async function verifySaved(scope: ExpenseCommandScope, saved: SavedPaymentCommand, current: () => void) {
  const stored = readPaymentCommand(scope, saved.batchId);
  if (!stored || stored.key !== saved.key || stored.digest !== saved.digest ||
      JSON.stringify(fingerprint(stored)) !== JSON.stringify(fingerprint(saved)) ||
      await payoutCommandDigest(fingerprint(saved)) !== saved.digest) throw new Error(UNVERIFIED);
  current();
}
function evidence(value: unknown, actorId: string): PaymentEvidence {
  const row = object(value);
  if (row.basis !== "operator_recorded" || row.bank_confirmation !== "unknown" ||
      row.recorded_by_id !== actorId || typeof row.recorded_at !== "string" ||
      !Number.isFinite(Date.parse(row.recorded_at))) throw new Error(UNVERIFIED);
  return row as unknown as PaymentEvidence;
}
function sameInstant(first: unknown, second: unknown): boolean {
  return typeof first === "string" && typeof second === "string" &&
    Number.isFinite(Date.parse(first)) && Number.isFinite(Date.parse(second)) && Date.parse(first) === Date.parse(second);
}
function paymentResult(value: unknown, saved: SavedPaymentCommand, actorId: string): PayoutBatchDetail {
  const batch = payoutBatchDetail(value);
  const ids = Object.keys(saved.payload.report_versions).sort();
  if (batch.id !== saved.batchId || batch.status !== "paid" || batch.version !== saved.payload.version + 1 ||
      batch.method !== saved.expectedMethod || batch.reference !== saved.payload.reference ||
      payoutMinor(batch.total_eur) !== payoutMinor(saved.payload.expected_total_eur) ||
      JSON.stringify(batch.reports.map((report) => report.id).sort()) !== JSON.stringify(ids) ||
      typeof batch.paid_at !== "string" || !Number.isFinite(Date.parse(batch.paid_at)) ||
      batch.reports.some((report) => report.status !== "reimbursed" || report.version !== saved.payload.report_versions[report.id] + 1 ||
        report.payment_reference !== saved.payload.reference || typeof report.reimbursed_at !== "string" || !Number.isFinite(Date.parse(report.reimbursed_at))) ||
      selectionTotal(versionedReports(batch)) !== saved.payload.expected_total_eur) throw new Error(UNVERIFIED);
  const proof = evidence(batch.payment_evidence, actorId);
  if (!sameInstant(proof.recorded_at, batch.paid_at) || batch.reports.some((report) => !sameInstant(report.reimbursed_at, batch.paid_at))) throw new Error(UNVERIFIED);
  return batch;
}
async function lookup(scope: ExpenseCommandScope, saved: SavedPaymentCommand, signal: AbortSignal, current: () => void) {
  try {
    const response = await api.get(`/reimbursements/commands/${encodeURIComponent(saved.key)}`, {
      signal, headers: payoutCommandHeaders(scope), timeout: PAYOUT_READ_TIMEOUT_MS,
    });
    current();
    const receipt = object(response.data);
    if (receipt.status !== "committed" || receipt.command_key !== saved.key || receipt.operation !== "reimbursement_paid" || receipt.target_id !== saved.batchId) throw new Error(UNVERIFIED);
    return paymentResult(receipt.result, saved, scope.userId);
  } catch (error) {
    current();
    if (axios.isAxiosError(error) && error.response?.status === 404) return null;
    throw error;
  }
}
export async function checkPaymentCommand(scope: ExpenseCommandScope, saved: SavedPaymentCommand, signal: AbortSignal) {
  const current = payoutCommandGuard(signal); current();
  await verifySaved(scope, saved, current);
  return lookup(scope, saved, signal, current);
}
export async function sendPaymentCommand(scope: ExpenseCommandScope, saved: SavedPaymentCommand, signal: AbortSignal, retry = false) {
  const current = payoutCommandGuard(signal); current();
  await verifySaved(scope, saved, current);
  if (retry) { const previous = await lookup(scope, saved, signal, current); if (previous) return previous; }
  let written: PayoutBatchDetail;
  try {
    const response = await api.post(`/reimbursements/${saved.batchId}/pay`, saved.payload, {
      signal, headers: { ...payoutCommandHeaders(scope), "Idempotency-Key": saved.key }, timeout: PAYOUT_READ_TIMEOUT_MS,
    });
    current(); written = paymentResult(response.data, saved, scope.userId);
  } catch (error) {
    current();
    if (axios.isAxiosError(error) && error.response &&
        ([400, 403, 404, 413, 422].includes(error.response.status) ||
          (error.response.status === 409 && ["reimbursement_payment_conflict", "reimbursement_selection_conflict", "reimbursement_total_conflict"].includes(apiErrorCode(error) ?? "")))) {
      clearPaymentCommand(scope, saved.batchId, saved.key);
    }
    throw error;
  }
  const committed = await lookup(scope, saved, signal, current);
  if (!committed || committed.id !== written.id) throw new Error(UNVERIFIED);
  return committed;
}
export async function readPaymentBatch(scope: ExpenseCommandScope, original: PayoutBatchDetail, saved: SavedPaymentCommand, signal: AbortSignal) {
  const current = payoutCommandGuard(signal); current();
  const response = await api.get(`/reimbursements/${saved.batchId}`, {
    signal, headers: payoutCommandHeaders(scope), timeout: PAYOUT_READ_TIMEOUT_MS,
  });
  current();
  const batch = paymentResult(response.data, saved, scope.userId);
  if (batch.payment_evidence?.basis !== original.payment_evidence?.basis ||
      batch.payment_evidence?.bank_confirmation !== original.payment_evidence?.bank_confirmation ||
      batch.payment_evidence?.recorded_by_id !== original.payment_evidence?.recorded_by_id ||
      !sameInstant(batch.payment_evidence?.recorded_at, original.payment_evidence?.recorded_at) ||
      !sameInstant(batch.paid_at, original.paid_at)) throw new Error(UNVERIFIED);
  return batch;
}
export function clearPaymentCommand(scope: ExpenseCommandScope, batchId: string, key: string) {
  if (readPaymentCommand(scope, batchId)?.key === key) sessionStorage.removeItem(slot(scope, batchId));
}
