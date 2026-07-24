import { cx } from "../../lib/cx";

export interface PaginationProps {
  /** 1-based current page. */
  page: number;
  /** Total number of rows across all pages. */
  total: number;
  pageSize: number;
  onPageChange: (page: number) => void;
  className?: string;
}

/**
 * List pagination. Renders a labelled `<nav>` with Previous / Next and a live
 * "showing X–Y of N" summary (announced via aria-live so screen-reader users hear
 * the range change). Page-number buttons are intentionally omitted for very large
 * datasets — prev/next + the range is clearer and keyboard-simpler for finance
 * lists. Buttons disable at the boundaries.
 */
export function Pagination({ page, total, pageSize, onPageChange, className = "" }: PaginationProps) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const current = Math.min(Math.max(1, page), pages);
  const from = total === 0 ? 0 : (current - 1) * pageSize + 1;
  const to = Math.min(current * pageSize, total);

  const btn =
    "inline-flex items-center gap-1 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 " +
    "hover:bg-slate-50 focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-brand-300 " +
    "disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-white";

  return (
    <nav aria-label="Pagination" className={cx("flex flex-wrap items-center justify-between gap-3", className)}>
      <p className="text-sm text-slate-500" aria-live="polite">
        {total === 0 ? (
          "No results"
        ) : (
          <>
            Showing <span className="font-medium tabular-nums text-slate-700">{from}</span>–
            <span className="font-medium tabular-nums text-slate-700">{to}</span> of{" "}
            <span className="font-medium tabular-nums text-slate-700">{total}</span>
          </>
        )}
      </p>
      <div className="flex items-center gap-2">
        <button type="button" className={btn} onClick={() => onPageChange(current - 1)} disabled={current <= 1}>
          <span aria-hidden="true">←</span> Previous
        </button>
        <span className="text-sm text-slate-400 tabular-nums">
          Page {current} of {pages}
        </span>
        <button type="button" className={btn} onClick={() => onPageChange(current + 1)} disabled={current >= pages}>
          Next <span aria-hidden="true">→</span>
        </button>
      </div>
    </nav>
  );
}
