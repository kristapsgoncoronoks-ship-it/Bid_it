import { Badge, type Tone } from "./Badge";

/**
 * Canonical finance status → tone + human label map. Centralises the mapping that
 * was scattered as ad-hoc colour dictionaries in `lib/format.ts`, so a status pill
 * looks identical wherever it appears. Keys cover supplier/customer invoices,
 * expenses, VAT claims, and payments. Unknown statuses fall back to a neutral pill
 * with a prettified label.
 */
export const STATUS_MAP: Record<string, { tone: Tone; label: string }> = {
  // Supplier / received invoices
  draft: { tone: "neutral", label: "Draft" },
  pending: { tone: "warning", label: "Pending" },
  approved: { tone: "info", label: "Approved" },
  paid: { tone: "success", label: "Paid" },
  overdue: { tone: "danger", label: "Overdue" },
  rejected: { tone: "danger", label: "Rejected" },
  // Customer / issued invoices
  open: { tone: "neutral", label: "Open" },
  partial: { tone: "info", label: "Partially paid" },
  credited: { tone: "brand", label: "Credited" },
  // Expenses
  submitted: { tone: "warning", label: "Submitted" },
  reimbursed: { tone: "success", label: "Reimbursed" },
  // Payments
  scheduled: { tone: "info", label: "Scheduled" },
  processing: { tone: "warning", label: "Processing" },
  completed: { tone: "success", label: "Completed" },
  failed: { tone: "danger", label: "Failed" },
  // VAT claims / generic
  ready: { tone: "success", label: "Ready" },
  filed: { tone: "info", label: "Filed" },
  awaiting: { tone: "warning", label: "Awaiting" },
  active: { tone: "success", label: "Active" },
  suspended: { tone: "neutral", label: "Suspended" },
};

function prettify(status: string): string {
  return status.replace(/[_-]/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

export interface StatusBadgeProps {
  status: string;
  /** Override the built-in map for a domain-specific vocabulary. */
  map?: Record<string, { tone: Tone; label: string }>;
}

/**
 * A `Badge` whose colour + label are derived from a status string, so callers pass
 * data (`status="overdue"`) rather than styling. The semantic tone also carries an
 * sr-only word ("status: Overdue") isn't needed since the label text is the status.
 */
export function StatusBadge({ status, map = STATUS_MAP }: StatusBadgeProps) {
  const entry = map[status] ?? { tone: "neutral" as Tone, label: prettify(status) };
  return <Badge tone={entry.tone}>{entry.label}</Badge>;
}
