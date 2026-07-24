// Accessible loading spinner. Announces itself to screen readers via role=status
// + an sr-only label; decorative to sighted users.
export function Spinner({ size = 16, label = "Loading" }: { size?: number; label?: string }) {
  return (
    <span role="status" aria-live="polite" className="inline-flex items-center">
      <svg
        className="animate-spin text-current"
        width={size}
        height={size}
        viewBox="0 0 24 24"
        fill="none"
        aria-hidden="true"
      >
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
        <path className="opacity-90" fill="currentColor" d="M4 12a8 8 0 0 1 8-8V0C5.4 0 0 5.4 0 12h4z" />
      </svg>
      <span className="sr-only">{label}</span>
    </span>
  );
}
