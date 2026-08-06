export function money(value: string | number, currency = "EUR"): string {
  const n = typeof value === "string" ? Number(value) : value;
  return new Intl.NumberFormat("en-IE", {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(Number.isFinite(n) ? n : 0);
}

// The currency's display symbol, derived once from Intl. `formatToParts(0)` is
// used ONLY to read the symbol out of the locale data — the zero is never a
// money value and never reaches the caller's amount.
const SYMBOL_CACHE: Record<string, string> = {};
function currencySymbol(currency: string): string {
  const cached = SYMBOL_CACHE[currency];
  if (cached !== undefined) return cached;
  let symbol = currency;
  try {
    const parts = new Intl.NumberFormat("en-IE", { style: "currency", currency }).formatToParts(0);
    symbol = parts.find((p) => p.type === "currency")?.value ?? currency;
  } catch {
    symbol = currency; // an unknown/invalid ISO code — show the code itself.
  }
  SYMBOL_CACHE[currency] = symbol;
  return symbol;
}

/**
 * Format a money amount that arrived on the wire as a DECIMAL STRING, without
 * ever turning it into a JavaScript number (master-context §4.9).
 *
 * The backend types every money field `Decimal` over `Numeric(14,2)`, and
 * pydantic v2 serializes it as a JSON string precisely so no float appears in a
 * money path. `money()` above re-parses with `Number()` — fine for the dashboard
 * aggregates it was written for, but a 14-digit `Numeric(14,2)` exceeds an IEEE
 * 754 double's 15–17 significant digits, so the last cents can change. This
 * formatter does string surgery instead: it groups the integer digits, keeps the
 * fraction digits the server sent, and performs NO arithmetic and NO rounding.
 *
 * `null`/`undefined`/`""` render as an em dash, never `0.00` — on a VAT claim a
 * null `vat_eur` means "not frozen yet", which is a different fact from zero.
 * A value that is not a plain decimal is returned unchanged (fail-open on
 * presentation: showing the server's own bytes beats swallowing them).
 */
export function decimalMoney(
  value: string | null | undefined,
  currency: string | null = "EUR",
): string {
  if (value === null || value === undefined || value === "") return "—";
  const m = /^([+-]?)(\d+)(?:\.(\d*))?$/.exec(value.trim());
  if (!m) return value;
  const [, rawSign, whole, frac = ""] = m;
  const sign = rawSign === "-" ? "-" : "";
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  // Pad to 2dp; NEVER truncate beyond it — more precision than the column can
  // hold is a fact worth showing, not a rounding decision for the UI to make.
  const cents = frac.length <= 2 ? (frac + "00").slice(0, 2) : frac;
  const symbol = currencySymbol(currency || "EUR");
  const gap = /^[A-Za-z]/.test(symbol) ? " " : "";
  return `${sign}${symbol}${gap}${grouped}.${cents}`;
}

export function compactMoney(value: string | number, currency = "EUR"): string {
  const n = typeof value === "string" ? Number(value) : value;
  return new Intl.NumberFormat("en-IE", {
    style: "currency",
    currency,
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(Number.isFinite(n) ? n : 0);
}

export function shortDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
}

export function monthLabel(period: string): string {
  // period is "YYYY-MM"
  const [y, m] = period.split("-");
  const d = new Date(Number(y), Number(m) - 1, 1);
  return d.toLocaleDateString("en-GB", { month: "short", year: "2-digit" });
}

export const STATUS_STYLES: Record<string, string> = {
  paid: "bg-emerald-100 text-emerald-700",
  pending: "bg-amber-100 text-amber-700",
  overdue: "bg-rose-100 text-rose-700",
  draft: "bg-slate-100 text-slate-600",
};

// Accounts-receivable status for issued invoices.
export const ISSUED_STATUS_STYLES: Record<string, string> = {
  draft: "bg-slate-100 text-slate-500",
  approved: "bg-indigo-100 text-indigo-700",
  sent: "bg-sky-50 text-sky-600",
  viewed: "bg-sky-100 text-sky-700",
  paid: "bg-emerald-100 text-emerald-700",
  partial: "bg-sky-100 text-sky-700",
  open: "bg-slate-100 text-slate-600",
  overdue: "bg-rose-100 text-rose-700",
  credited: "bg-violet-100 text-violet-700",
  credit_note: "bg-violet-100 text-violet-700",
  disputed: "bg-amber-100 text-amber-700",
  written_off: "bg-slate-200 text-slate-500",
  void: "bg-slate-200 text-slate-500 line-through",
};

export const ISSUED_STATUS_LABELS: Record<string, string> = {
  draft: "Draft", approved: "Approved", sent: "Sent", viewed: "Viewed",
  paid: "Paid", partial: "Partially paid", open: "Open", overdue: "Overdue",
  credited: "Credited", credit_note: "Credit note",
  disputed: "Disputed", written_off: "Written off", void: "Cancelled",
};

export const VALIDATION_STYLES: Record<string, string> = {
  none: "bg-slate-100 text-slate-500",
  passed: "bg-emerald-100 text-emerald-700",
  approved: "bg-emerald-100 text-emerald-700",
  flagged: "bg-rose-100 text-rose-700",
  rejected: "bg-rose-100 text-rose-700",
  pending: "bg-amber-100 text-amber-700",
};

export const VALIDATION_LABELS: Record<string, string> = {
  none: "no validation",
  passed: "AI passed",
  flagged: "flagged",
  pending: "awaiting review",
  approved: "approved",
  rejected: "rejected",
};

// How an invoice was extracted — shown on received/uploaded invoices.
export const METHOD_LABELS: Record<string, string> = {
  "e-invoice-xml": "E-invoice XML (exact)",
  "text-layer": "PDF text layer",
  ocr: "OCR (scanned)",
  csv: "CSV",
  json: "JSON",
  unknown: "—",
};

export function methodLabel(method: string | null | undefined): string {
  return METHOD_LABELS[method ?? "unknown"] ?? method ?? "—";
}

export const METHOD_STYLES: Record<string, string> = {
  ocr: "bg-violet-100 text-violet-700",
  "text-layer": "bg-sky-100 text-sky-700",
  "e-invoice-xml": "bg-emerald-100 text-emerald-700",
  csv: "bg-slate-100 text-slate-600",
  json: "bg-slate-100 text-slate-600",
};

// Categorical chart palette (was duplicated in Charts, Dashboard, Explore).
export const CHART_PALETTE = [
  "#3b6ef2", "#22c55e", "#f59e0b", "#ef4444", "#8b5cf6",
  "#06b6d4", "#ec4899", "#64748b", "#14b8a6", "#f97316",
];

// Email-intake inbound review statuses.
export const INBOUND_STATUS_STYLES: Record<string, string> = {
  queued: "bg-sky-100 text-sky-700",       // stored, awaiting worker extraction
  pending: "bg-amber-100 text-amber-700",
  confirmed: "bg-emerald-100 text-emerald-700",
  failed: "bg-rose-100 text-rose-700",
  rejected: "bg-rose-100 text-rose-700",
  discarded: "bg-slate-100 text-slate-500",
};

export const SEVERITY_STYLES: Record<string, string> = {
  error: "bg-rose-100 text-rose-700",
  warning: "bg-amber-100 text-amber-700",
  info: "bg-sky-100 text-sky-700",
};

export const EXPENSE_STATUS_STYLES: Record<string, string> = {
  draft: "bg-slate-100 text-slate-600",
  submitted: "bg-amber-100 text-amber-700",
  partially_approved: "bg-amber-100 text-amber-800",
  approved: "bg-sky-100 text-sky-700",
  returned: "bg-orange-100 text-orange-700",
  marked_for_reimbursement: "bg-indigo-100 text-indigo-700",
  reimbursed: "bg-emerald-100 text-emerald-700",
  rejected: "bg-rose-100 text-rose-700",
};
