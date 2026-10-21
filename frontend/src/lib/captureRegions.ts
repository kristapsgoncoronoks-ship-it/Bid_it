import type { InvoiceCreate } from "./types";

export interface RegionRect { x: number; y: number; width: number; height: number }
export type RegionRotation = 0 | 90 | 180 | 270;
export interface RegionReadRequest extends RegionRect {
  command_key: string; expected_attempt_seq: number; source_sha256: string;
  page: number; rotation: RegionRotation; language: "eng"; target: "field" | "table";
  field?: string; line_index?: number;
}
export interface RegionField { field: string; line_index?: number | null; value: string }
export interface RegionResult {
  text: string; engine: string; fields: RegionField[];
  rows: Record<string, string | null>[]; warnings: string[];
}
export interface RegionRead {
  id: string; command_key: string; extraction_run_id: string;
  status: "queued" | "running" | "ready" | "failed" | "stale" | "applied";
  request: RegionReadRequest; result: RegionResult | null;
  error_code: string | null; error_detail: string | null;
  review_fingerprint: string; current_draft: InvoiceCreate;
  reviewed_fields: { field: string; line_index?: number | null }[];
  applied: boolean; apply_command_key?: string | null;
  applied_result?: { draft: InvoiceCreate; review_fingerprint: string; changed_fields: number; appended_rows: number } | null;
}
export interface RegionHistory {
  items: RegionRead[]; source_sha256: string | null; attempt_seq: number; eligible: boolean; reason: string | null;
  current_draft: InvoiceCreate; review_fingerprint: string;
  reviewed_fields: { field: string; line_index?: number | null }[];
}
export interface RegionApply {
  command_key: string; expected_review_fingerprint: string; fields: RegionField[];
  rows: { candidate_index: number; target_line_index: number | null; fields: string[] }[];
  overwrite_reviewed: boolean;
}

export function validRect(rect: RegionRect): boolean {
  return Object.values(rect).every(Number.isFinite) && rect.x >= 0 && rect.y >= 0 &&
    rect.width >= 0.005 && rect.height >= 0.005 && rect.x + rect.width <= 1.000001 && rect.y + rect.height <= 1.000001;
}
export function regionFromPoints(start: { x: number; y: number }, end: { x: number; y: number }): RegionRect {
  const clamp = (value: number) => Math.max(0, Math.min(1, value));
  const a = { x: clamp(start.x), y: clamp(start.y) }, b = { x: clamp(end.x), y: clamp(end.y) };
  return { x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), width: Math.abs(a.x - b.x), height: Math.abs(a.y - b.y) };
}
export function fieldKey(field: string, lineIndex?: number | null): string { return `${lineIndex ?? "header"}:${field}`; }
export function currentRegionValue(draft: InvoiceCreate, field: string, lineIndex?: number | null): string {
  const record = lineIndex == null ? draft : draft.line_items[lineIndex];
  const value = (record as unknown as Record<string, unknown> | undefined)?.[field];
  return value == null ? "" : String(value);
}
export function validateRegionRead(value: unknown, runId: string, commandKey?: string): RegionRead {
  const row = value as RegionRead;
  if (!row || typeof row.id !== "string" || row.extraction_run_id !== runId ||
      typeof row.command_key !== "string" || (commandKey && row.command_key !== commandKey) ||
      !["queued", "running", "ready", "failed", "stale", "applied"].includes(row.status) ||
      !row.current_draft || !Array.isArray(row.current_draft.line_items) ||
      typeof row.review_fingerprint !== "string" || !Array.isArray(row.reviewed_fields) ||
      (row.result && (!Array.isArray(row.result.fields) || !Array.isArray(row.result.rows) || typeof row.result.text !== "string"))) {
    throw new Error("The re-read response could not be verified. Check its status before continuing.");
  }
  return row;
}

export const REGION_HEADERS = [
  ["invoice_number", "Invoice number"], ["vendor_name", "Supplier name"], ["issue_date", "Issue date"],
  ["due_date", "Due date"], ["currency", "Currency"], ["po_reference", "PO reference"],
  ["supplier_registration_number", "Supplier registration number"], ["supplier_vat_id", "Supplier VAT ID"],
  ["supplier_iban", "Supplier IBAN"],
] as const;
export const REGION_LINES = [
  ["description", "Description"], ["quantity", "Quantity"], ["unit_price", "Unit price"],
  ["amount", "Net amount"], ["tax_rate", "Tax rate"], ["captured_tax_amount", "Captured tax"],
  ["captured_gross_amount", "Captured gross"], ["category", "Category"],
] as const;
