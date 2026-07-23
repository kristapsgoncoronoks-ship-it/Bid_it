export function money(value: string | number, currency = "EUR"): string {
  const n = typeof value === "string" ? Number(value) : value;
  return new Intl.NumberFormat("en-IE", {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(Number.isFinite(n) ? n : 0);
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
  paid: "bg-emerald-100 text-emerald-700",
  partial: "bg-sky-100 text-sky-700",
  open: "bg-slate-100 text-slate-600",
  overdue: "bg-rose-100 text-rose-700",
  credited: "bg-violet-100 text-violet-700",
  credit_note: "bg-violet-100 text-violet-700",
  void: "bg-slate-200 text-slate-500 line-through",
};

export const ISSUED_STATUS_LABELS: Record<string, string> = {
  paid: "Paid", partial: "Partially paid", open: "Open", overdue: "Overdue",
  credited: "Credited", credit_note: "Credit note", void: "Void",
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
  approved: "bg-sky-100 text-sky-700",
  returned: "bg-orange-100 text-orange-700",
  marked_for_reimbursement: "bg-indigo-100 text-indigo-700",
  reimbursed: "bg-emerald-100 text-emerald-700",
  rejected: "bg-rose-100 text-rose-700",
};
