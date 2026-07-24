import { useId, type ReactNode } from "react";
import { cx } from "../../lib/cx";

/**
 * A horizontal toolbar that groups a search box, filter selects, and a clear
 * action. Purely a layout + a11y container (`role="search"` region with a label);
 * the caller supplies the controls, usually `SearchInput` + `FilterSelect`s.
 * Wraps to multiple rows on narrow screens.
 */
export function FilterBar({
  children,
  label = "Filters",
  className = "",
}: {
  children: ReactNode;
  label?: string;
  className?: string;
}) {
  return (
    <div
      role="search"
      aria-label={label}
      className={cx(
        "flex flex-wrap items-end gap-3 rounded-xl border border-slate-200 bg-white p-3 shadow-xs",
        className,
      )}
    >
      {children}
    </div>
  );
}

export interface FilterSelectProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
  /** Visually hide the label (still read by screen readers). */
  hideLabel?: boolean;
}

/**
 * A labelled `<select>` sized for the filter bar. Always has an accessible name
 * (the label), visible by default.
 */
export function FilterSelect({ label, value, onChange, options, hideLabel }: FilterSelectProps) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className={hideLabel ? "sr-only" : "text-xs font-medium text-slate-500"}>
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 outline-hidden focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
  );
}

/**
 * A dismissible "active filter" chip — shows the filters currently applied so a
 * user can see and remove them one by one.
 */
export function FilterChip({ label, onRemove }: { label: ReactNode; onRemove: () => void }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-brand-50 py-1 pl-3 pr-1 text-xs font-medium text-brand-700">
      {label}
      <button
        type="button"
        onClick={onRemove}
        aria-label={`Remove filter`}
        className="grid h-4 w-4 place-items-center rounded-full text-brand-500 hover:bg-brand-100 hover:text-brand-700 focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-brand-300"
      >
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
        </svg>
      </button>
    </span>
  );
}
