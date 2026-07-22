/**
 * A loud, persistent notice that everything below is placeholder data. Present on
 * every `/design` screen so the fixtures can never be mistaken for a real,
 * populated finance dashboard.
 */
export function DevBanner() {
  return (
    <div
      role="note"
      className="flex items-center gap-2 border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs font-medium text-amber-800"
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true" className="shrink-0">
        <path d="M12 8v5M12 16.5v.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        <path d="M10.3 3.9L2.4 18a2 2 0 001.7 3h15.8a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
      </svg>
      Development fixtures — every figure here is placeholder data for demonstrating the design system, not real financial data.
    </div>
  );
}
