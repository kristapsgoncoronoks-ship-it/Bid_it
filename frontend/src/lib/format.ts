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

export const SEVERITY_STYLES: Record<string, string> = {
  error: "bg-rose-100 text-rose-700",
  warning: "bg-amber-100 text-amber-700",
  info: "bg-sky-100 text-sky-700",
};

export const EXPENSE_STATUS_STYLES: Record<string, string> = {
  draft: "bg-slate-100 text-slate-600",
  submitted: "bg-amber-100 text-amber-700",
  approved: "bg-sky-100 text-sky-700",
  reimbursed: "bg-emerald-100 text-emerald-700",
  rejected: "bg-rose-100 text-rose-700",
};
