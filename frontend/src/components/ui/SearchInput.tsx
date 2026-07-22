import { useId } from "react";
import { cx } from "../../lib/cx";

export interface SearchInputProps {
  value: string;
  onChange: (value: string) => void;
  /** Accessible label (visually hidden). */
  label?: string;
  placeholder?: string;
  /** Fires on Enter, for search-on-submit flows. */
  onSubmit?: (value: string) => void;
  className?: string;
  autoFocus?: boolean;
}

/**
 * The standard search control: a `role="searchbox"` input with a leading magnifier,
 * a hidden label, and a clear (×) button that appears once there's text. Debouncing
 * is the caller's concern (keep it controlled) so this stays presentational.
 */
export function SearchInput({
  value,
  onChange,
  label = "Search",
  placeholder = "Search…",
  onSubmit,
  className = "",
  autoFocus,
}: SearchInputProps) {
  const id = useId();
  return (
    <div className={cx("relative", className)}>
      <label htmlFor={id} className="sr-only">
        {label}
      </label>
      <span className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3 text-slate-400" aria-hidden="true">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
          <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="2" />
          <path d="M20 20l-3.5-3.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        </svg>
      </span>
      <input
        id={id}
        type="search"
        role="searchbox"
        value={value}
        autoFocus={autoFocus}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && onSubmit) onSubmit(value);
        }}
        className="w-full rounded-lg border border-slate-300 py-2 pl-9 pr-9 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
      />
      {value && (
        <button
          type="button"
          onClick={() => onChange("")}
          aria-label="Clear search"
          className="absolute inset-y-0 right-0 flex items-center pr-3 text-slate-400 hover:text-slate-600 focus-visible:outline-none focus-visible:text-brand-600"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
        </button>
      )}
    </div>
  );
}
