import type { ReactNode } from "react";
import { Button } from "./Button";

/**
 * The canonical error surface — distinct from `EmptyState` (which means "no data
 * yet, that's fine"). This means "something went wrong". Announces via
 * `role="alert"`, explains what failed in plain language, and offers a retry.
 * Use it for a failed load; keep destructive-sounding language out of empty states.
 */
export function ErrorState({
  title = "Something went wrong",
  description = "We couldn’t load this. Check your connection and try again.",
  onRetry,
  retryLabel = "Try again",
  className = "",
}: {
  title?: string;
  description?: ReactNode;
  onRetry?: () => void;
  retryLabel?: string;
  className?: string;
}) {
  return (
    <div role="alert" className={`flex flex-col items-center justify-center px-6 py-10 text-center ${className}`}>
      <div className="mb-3 grid h-10 w-10 place-items-center rounded-full bg-rose-100 text-rose-600" aria-hidden="true">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
          <path d="M12 8v5M12 16.5v.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          <path d="M10.3 3.9L2.4 18a2 2 0 001.7 3h15.8a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
        </svg>
      </div>
      <p className="text-sm font-semibold text-slate-700">{title}</p>
      <p className="mt-1 max-w-sm text-sm text-slate-500">{description}</p>
      {onRetry && (
        <div className="mt-4">
          <Button variant="secondary" size="sm" onClick={onRetry}>
            {retryLabel}
          </Button>
        </div>
      )}
    </div>
  );
}
