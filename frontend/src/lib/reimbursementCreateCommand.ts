import axios from "axios";
import { api, apiErrorCode, tokenStore } from "./api";
import type { ExpenseCommandScope } from "./manualExpenseCommand";

export interface PayoutReport {
  id: string;
  title: string;
  employee_name: string;
  total: string;
  currency: string;
  total_eur: string | null;
  status: string;
  version: number;
}
export interface PayoutBatch {
  id: string;
  reference: string | null;
  method: string;
  status: string;
  total_eur: string;
  paid_at: string | null;
  created_by: string | null;
  exported_at: string | null;
  export_count: number;
  last_msg_id: string | null;
  version: number;
  created_at: string;
  report_count: number;
}
export interface PayoutBatchDetail extends PayoutBatch {
  reports: Array<Omit<PayoutReport, "version">>;
}
export interface BatchCreatePayload {
  report_ids: string[];
  report_versions: Record<string, number>;
  method: "bank_transfer";
  note: null;
}
export interface SavedBatchCommand {
  key: string;
  digest: string;
  payload: BatchCreatePayload;
  expectedTotal: string;
}
export const PAYOUT_READ_TIMEOUT_MS = 15_000;
const MAX_MINOR = 99999999999999n;
const ID = /^[A-Za-z0-9-]{1,120}$/;
const UNREADABLE = "The saved payout request could not be verified. Keep its reference and ask an administrator before creating another batch.";

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(UNREADABLE);
  return value as Record<string, unknown>;
}

/** Wire money stays decimal text. Unknown conversion is never raw foreign money. */
export function payoutMinor(value: unknown): bigint {
  if (typeof value !== "string" || value.length > 32 || !/^\d+(?:\.\d{1,2})?$/.test(value)) {
    throw new Error("The EUR amount could not be verified. Refresh the report before selecting it.");
  }
  const [whole, fraction = ""] = value.split(".");
  const result = BigInt(whole) * 100n + BigInt(fraction.padEnd(2, "0"));
  if (result > MAX_MINOR) throw new Error("The payout total is too large. Split the selection into separate batches.");
  return result;
}

export function payoutDecimal(value: bigint): string {
  return `${value / 100n}.${String(value % 100n).padStart(2, "0")}`;
}

export function reportEUR(report: Pick<PayoutReport, "total_eur" | "total" | "currency">): string | null {
  const value = report.total_eur ?? (report.currency === "EUR" ? report.total : null);
  if (value === null) return null;
  try { return payoutDecimal(payoutMinor(value)); }
  catch { return null; }
}

export function selectionTotal(reports: PayoutReport[]): string {
  let total = 0n;
  for (const report of reports) {
    const amount = reportEUR(report);
    if (amount === null) throw new Error("A selected report has no verified EUR conversion. Refresh and review the selection.");
    total += payoutMinor(amount);
  }
  if (total > MAX_MINOR) throw new Error("The payout total is too large. Split the selection into separate batches.");
  return payoutDecimal(total);
}

function slot(scope: ExpenseCommandScope): string {
  // Same tenant/actor/action namespace as invoiceCommands; one unresolved create.
  return `invoiceiq:command:${scope.userId}:${scope.orgId}:/reimbursements`;
}

function payload(value: unknown): BatchCreatePayload {
  const row = object(value);
  if (!Array.isArray(row.report_ids) || row.report_ids.length < 1 || row.report_ids.length > 1000 ||
      row.report_ids.some((id) => typeof id !== "string" || !ID.test(id)) ||
      new Set(row.report_ids).size !== row.report_ids.length || row.method !== "bank_transfer" || row.note !== null) {
    throw new Error(UNREADABLE);
  }
  const ids = row.report_ids as string[];
  const versions = object(row.report_versions);
  if (Object.keys(versions).length !== ids.length || ids.some((id) => !Number.isInteger(versions[id]) || (versions[id] as number) < 1)) {
    throw new Error(UNREADABLE);
  }
  const ordered = [...ids].sort();
  return Object.freeze({ report_ids: Object.freeze(ordered) as unknown as string[],
    report_versions: Object.freeze(Object.fromEntries(ordered.map((id) => [id, versions[id] as number]))),
    method: "bank_transfer", note: null });
}

export function readPayoutCommand(scope: ExpenseCommandScope): SavedBatchCommand | null {
  const stored = sessionStorage.getItem(slot(scope));
  if (stored === null) return null;
  try {
    const row = object(JSON.parse(stored));
    if (typeof row.key !== "string" || !/^[A-Za-z0-9:_-]{1,120}$/.test(row.key) ||
        typeof row.digest !== "string" || !/^[a-f0-9]{64}$/.test(row.digest)) throw new Error(UNREADABLE);
    return { key: row.key, digest: row.digest, payload: payload(row.payload),
      expectedTotal: payoutDecimal(payoutMinor(row.expectedTotal)) };
  } catch { throw new Error(UNREADABLE); }
}

function guard(signal: AbortSignal): () => void {
  const generation = tokenStore.generation();
  const token = tokenStore.get();
  return () => {
    if (signal.aborted || generation !== tokenStore.generation() || token !== tokenStore.get()) {
      throw new axios.CanceledError("The payout request was interrupted. Check its saved reference in the original account and company.");
    }
  };
}

async function digest(value: BatchCreatePayload): Promise<string> {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify(value)));
  return Array.from(new Uint8Array(bytes), (b) => b.toString(16).padStart(2, "0")).join("");
}

function headers(scope: ExpenseCommandScope): Record<string, string> {
  return { "X-Expected-Org-ID": scope.orgId, "X-Expected-User-ID": scope.userId };
}

export async function preparePayoutCommand(
  scope: ExpenseCommandScope, reports: PayoutReport[], signal: AbortSignal,
): Promise<SavedBatchCommand> {
  const current = guard(signal);
  current();
  if (readPayoutCommand(scope)) throw new Error("Check the saved payout request before creating another batch.");
  const body = payload({ report_ids: reports.map((report) => report.id),
    report_versions: Object.fromEntries(reports.map((report) => [report.id, report.version])),
    method: "bank_transfer", note: null });
  const expectedTotal = selectionTotal(reports);
  const hash = await digest(body);
  current();
  if (readPayoutCommand(scope)) throw new Error("Check the saved payout request before creating another batch.");
  const saved = { key: crypto.randomUUID(), digest: hash, payload: body, expectedTotal };
  sessionStorage.setItem(slot(scope), JSON.stringify(saved));
  return saved;
}

function detail(value: unknown): PayoutBatchDetail {
  const row = object(value);
  if (typeof row.id !== "string" || !ID.test(row.id) ||
      !["open", "paid", "cancelled"].includes(String(row.status)) ||
      !Number.isInteger(row.version) || (row.version as number) < 1 ||
      !Number.isInteger(row.report_count) || (row.report_count as number) < 0 ||
      !Number.isInteger(row.export_count) ||
      !(row.exported_at === null || typeof row.exported_at === "string") ||
      !(row.last_msg_id === null || typeof row.last_msg_id === "string") ||
      !Array.isArray(row.reports) || row.reports.length !== row.report_count) throw new Error(UNREADABLE);
  payoutMinor(row.total_eur);
  const ids = row.reports.map((report) => object(report).id);
  if (ids.some((id) => typeof id !== "string" || !ID.test(id)) || new Set(ids).size !== ids.length) throw new Error(UNREADABLE);
  return row as unknown as PayoutBatchDetail;
}

function originalResult(value: unknown, saved: SavedBatchCommand): PayoutBatchDetail {
  const batch = detail(value);
  const actual = batch.reports.map((report) => report.id).sort();
  if (batch.status !== "open" || batch.method !== saved.payload.method ||
      JSON.stringify(actual) !== JSON.stringify(saved.payload.report_ids) ||
      payoutDecimal(payoutMinor(batch.total_eur)) !== saved.expectedTotal) throw new Error(UNREADABLE);
  // Verify each returned conversion as well as the immutable aggregate/selection.
  if (selectionTotal(batch.reports as PayoutReport[]) !== saved.expectedTotal) throw new Error(UNREADABLE);
  return batch;
}

async function verifiedSaved(scope: ExpenseCommandScope, saved: SavedBatchCommand, current: () => void): Promise<void> {
  const stored = readPayoutCommand(scope);
  if (!stored || stored.key !== saved.key || stored.digest !== saved.digest ||
      JSON.stringify(stored.payload) !== JSON.stringify(saved.payload) || stored.expectedTotal !== saved.expectedTotal) throw new Error(UNREADABLE);
  if (await digest(saved.payload) !== saved.digest) throw new Error(UNREADABLE);
  current();
}

async function lookup(
  scope: ExpenseCommandScope, saved: SavedBatchCommand, signal: AbortSignal, current: () => void,
): Promise<PayoutBatchDetail | null> {
  try {
    const response = await api.get(`/reimbursements/commands/${encodeURIComponent(saved.key)}`, {
      headers: headers(scope), signal, timeout: PAYOUT_READ_TIMEOUT_MS,
    });
    current();
    const receipt = object(response.data);
    if (receipt.status !== "committed" || receipt.command_key !== saved.key ||
        receipt.operation !== "reimbursement_created" || typeof receipt.target_id !== "string") throw new Error(UNREADABLE);
    const batch = originalResult(receipt.result, saved);
    if (batch.id !== receipt.target_id) throw new Error(UNREADABLE);
    return batch;
  } catch (error) {
    current();
    if (axios.isAxiosError(error) && error.response?.status === 404) return null;
    throw error;
  }
}

/** GET-only recovery never creates a replacement batch. */
export async function checkPayoutCommand(
  scope: ExpenseCommandScope, saved: SavedBatchCommand, signal: AbortSignal,
): Promise<PayoutBatchDetail | null> {
  const current = guard(signal);
  current();
  await verifiedSaved(scope, saved, current);
  return lookup(scope, saved, signal, current);
}

/** Retries are explicit and first check the original key; no automatic POST retry. */
export async function sendPayoutCommand(
  scope: ExpenseCommandScope, saved: SavedBatchCommand, signal: AbortSignal, retry = false,
): Promise<PayoutBatchDetail> {
  const current = guard(signal);
  current();
  await verifiedSaved(scope, saved, current);
  if (retry) {
    const previous = await lookup(scope, saved, signal, current);
    if (previous) return previous;
  }
  current();
  let written: PayoutBatchDetail;
  try {
    const response = await api.post("/reimbursements", saved.payload, {
      headers: { ...headers(scope), "Idempotency-Key": saved.key }, signal, timeout: PAYOUT_READ_TIMEOUT_MS,
    });
    current();
    written = originalResult(response.data, saved);
  } catch (error) {
    current();
    // These are definite precommit refusals. An uncertain transport/5xx or a
    // key/payload conflict retains its reference and frozen original inputs.
    if (axios.isAxiosError(error) && error.response &&
        ([400, 403, 404, 413, 422].includes(error.response.status) ||
          (error.response.status === 409 && apiErrorCode(error) === "reimbursement_selection_conflict"))) {
      clearPayoutCommand(scope, saved.key);
    }
    throw error;
  }
  const committed = await lookup(scope, saved, signal, current);
  if (!committed || committed.id !== written.id) throw new Error("The committed payout receipt could not be verified. Check the saved request before another batch.");
  return committed;
}

/** The caller retains the command until this current-state read and list refresh finish. */
export async function readPayoutBatch(
  scope: ExpenseCommandScope, original: PayoutBatchDetail, signal: AbortSignal,
): Promise<PayoutBatchDetail> {
  const current = guard(signal);
  current();
  const response = await api.get(`/reimbursements/${original.id}`, {
    headers: headers(scope), signal, timeout: PAYOUT_READ_TIMEOUT_MS,
  });
  current();
  const batch = detail(response.data);
  if (batch.id !== original.id || payoutMinor(batch.total_eur) !== payoutMinor(original.total_eur)) throw new Error(UNREADABLE);
  if (batch.status !== "cancelled" && JSON.stringify(batch.reports.map((report) => report.id).sort()) !==
      JSON.stringify(original.reports.map((report) => report.id).sort())) throw new Error(UNREADABLE);
  if (batch.status === "cancelled" && batch.reports.length !== 0) throw new Error(UNREADABLE);
  return batch;
}

export function clearPayoutCommand(scope: ExpenseCommandScope, key: string): void {
  if (readPayoutCommand(scope)?.key === key) sessionStorage.removeItem(slot(scope));
}
