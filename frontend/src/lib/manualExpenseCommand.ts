import type { ExpenseItemInput } from "./types";

export const MANUAL_EXPENSE_SLOT = "/expenses/manual";
export type ExpenseCommandScope = { userId: string; orgId: string };
export type ManualDraftPayload = { title: string; currency: string; items: ExpenseItemInput[] };
export type SavedManualDraft = { id: string; title: string; currency: string; total: string; vat_total: string };
const MAX_CENTS = 99999999999999n;

/** The manual editor supports cents only. Legacy API rounding is unchanged. */
function cents(value: string, label: string): bigint {
  const text = value.trim();
  if (text.length > 32 || !/^\d+(?:\.\d{1,2})?$/.test(text)) {
    throw new Error(`${label}: enter a nonnegative amount with at most two decimal places.`);
  }
  const [whole, fraction = ""] = text.split(".");
  const amount = BigInt(whole) * 100n + BigInt(fraction.padEnd(2, "0"));
  if (amount > MAX_CENTS) throw new Error(`${label}: the maximum is 999999999999.99.`);
  return amount;
}

function decimal(value: bigint): string {
  return `${value / 100n}.${String(value % 100n).padStart(2, "0")}`;
}

export function manualDraftAmounts(items: ExpenseItemInput[]): { total: string; vat: string; items: ExpenseItemInput[] } {
  let total = 0n;
  let vat = 0n;
  const normalized = items.map((item, i) => {
    const amount = cents(item.amount, `Item ${i + 1} amount`);
    const tax = cents(item.vat_amount, `Item ${i + 1} VAT`);
    if (tax > amount) throw new Error(`Item ${i + 1} VAT cannot exceed its amount.`);
    total += amount;
    vat += tax;
    return { ...item, amount: decimal(amount), vat_amount: decimal(tax) };
  });
  if (total > MAX_CENTS || vat > MAX_CENTS) throw new Error("Report totals cannot exceed 999999999999.99. Split this into separate reports.");
  return { total: decimal(total), vat: decimal(vat), items: normalized };
}

/** Copy and freeze synchronously in the click handler, before hashing can yield. */
export function captureManualDraft(title: string, currency: string, items: ExpenseItemInput[]): ManualDraftPayload {
  if (!title.trim() || title.trim().length > 200) throw new Error("Enter a report title of up to 200 characters.");
  const code = currency.trim().toUpperCase();
  if (!/^[A-Z]{3}$/.test(code)) throw new Error("Currency must be a three-letter code.");
  if (!items.length) throw new Error("Add at least one expense.");
  const normalized = manualDraftAmounts(items).items;
  normalized.forEach((item, i) => {
    if (!item.description.trim() || item.description.length > 300) throw new Error(`Item ${i + 1} description is required (up to 300 characters).`);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(item.spend_date)) throw new Error(`Item ${i + 1} spend date is required.`);
    Object.freeze(item);
  });
  Object.freeze(normalized);
  return Object.freeze({ title: title.trim(), currency: code, items: normalized });
}

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("The manual save response could not be verified. Keep its reference and check its outcome again.");
  return value as Record<string, unknown>;
}

function savedDraft(value: unknown): SavedManualDraft {
  const report = object(value);
  if (typeof report.id !== "string" || !/^[A-Za-z0-9-]{1,120}$/.test(report.id) ||
      typeof report.title !== "string" || !report.title || report.title.length > 200 ||
      typeof report.currency !== "string" || !/^[A-Z]{3}$/.test(report.currency) ||
      typeof report.total !== "string" || typeof report.vat_total !== "string") {
    throw new Error("The manual save response could not be verified. Keep its reference and check its outcome again.");
  }
  const total = cents(report.total, "Saved report total");
  const vat = cents(report.vat_total, "Saved report VAT");
  if (vat > total) throw new Error("The saved report totals could not be verified. Check its outcome again.");
  return { id: report.id, title: report.title, currency: report.currency, total: decimal(total), vat_total: decimal(vat) };
}

/** A receipt proves the original save, independently of the report's current state. */
export function manualDraftResponse(value: unknown, key: string, source: "lookup" | "write"): SavedManualDraft {
  if (source === "write") return savedDraft(value);
  const receipt = object(value);
  if (receipt.status !== "committed" || receipt.operation !== "report_created" ||
      receipt.command_key !== key || typeof receipt.target_id !== "string" || !receipt.target_id) {
    throw new Error("The manual save receipt could not be verified. Keep its reference and check its outcome again.");
  }
  const report = savedDraft(receipt.result);
  if (report.id !== receipt.target_id) throw new Error("The manual save receipt points to a different report. Keep its reference and ask an administrator.");
  return report;
}

export function manualExpenseLookup(key: string): string {
  return `/expenses/commands/${encodeURIComponent(key)}`;
}

export function readManualReference(scope: ExpenseCommandScope): { key: string; digest: string } | null {
  const stored = sessionStorage.getItem(`invoiceiq:command:${scope.userId}:${scope.orgId}:${MANUAL_EXPENSE_SLOT}`);
  if (stored === null) return null;
  try {
    const value = object(JSON.parse(stored));
    if (typeof value.key === "string" && /^[A-Za-z0-9:_-]{1,120}$/.test(value.key) &&
        typeof value.digest === "string" && /^[a-f0-9]{64}$/.test(value.digest)) {
      return { key: value.key, digest: value.digest };
    }
  } catch { /* Never replace unreadable evidence with a fresh financial command. */ }
  throw new Error("The previous manual save reference is unreadable. Check My reports and ask an administrator before creating another draft.");
}
