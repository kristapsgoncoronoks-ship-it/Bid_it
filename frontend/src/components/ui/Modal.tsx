import { useEffect, useId, type ReactNode } from "react";
import { Portal } from "./Portal";
import { useFocusTrap } from "./useFocusTrap";
import { cx } from "../../lib/cx";

export type ModalSize = "sm" | "md" | "lg";

const SIZES: Record<ModalSize, string> = {
  sm: "max-w-sm",
  md: "max-w-lg",
  lg: "max-w-2xl",
};

export interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  /** Optional supporting line under the title. */
  description?: ReactNode;
  children?: ReactNode;
  /** Footer actions (right-aligned). Usually a cancel + a confirm Button. */
  footer?: ReactNode;
  size?: ModalSize;
  /** Clicking the backdrop closes by default; set false for destructive flows. */
  closeOnBackdrop?: boolean;
}

/**
 * Accessible modal dialog. Implements the full WAI-ARIA dialog contract:
 * `role="dialog"` + `aria-modal`, labelled by the title (and described by the
 * description when present), focus trapped inside and restored on close, Escape
 * to dismiss, background scroll locked, and a click-catching backdrop.
 *
 * Content-agnostic: pass any `children` (a form, a confirmation, a summary). For
 * a yes/no confirmation use `ConfirmDialog`, which composes this.
 */
export function Modal({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  size = "md",
  closeOnBackdrop = true,
}: ModalProps) {
  const titleId = useId();
  const descId = useId();
  const trapRef = useFocusTrap<HTMLDivElement>(open, onClose);

  // Lock background scroll while open.
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  if (!open) return null;

  return (
    <Portal>
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
        {/* Backdrop — decorative; the dialog owns keyboard semantics. */}
        <div
          className="iq-fade-in absolute inset-0 bg-slate-900/40"
          aria-hidden="true"
          onClick={closeOnBackdrop ? onClose : undefined}
        />
        <div
          ref={trapRef}
          role="dialog"
          aria-modal="true"
          aria-labelledby={titleId}
          aria-describedby={description ? descId : undefined}
          tabIndex={-1}
          className={cx(
            "iq-pop-in relative z-10 w-full rounded-xl border border-slate-200 bg-white shadow-xl outline-hidden",
            SIZES[size],
          )}
        >
          <header className="flex items-start justify-between gap-4 border-b border-slate-100 px-5 py-4">
            <div>
              <h2 id={titleId} className="text-base font-semibold text-slate-800">
                {title}
              </h2>
              {description && (
                <p id={descId} className="mt-1 text-sm text-slate-500">
                  {description}
                </p>
              )}
            </div>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close dialog"
              className="-mr-1 rounded-lg p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600 focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-brand-300"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
              </svg>
            </button>
          </header>

          {children && <div className="px-5 py-4 text-sm text-slate-600">{children}</div>}

          {footer && (
            <footer className="flex items-center justify-end gap-2 border-t border-slate-100 px-5 py-3">
              {footer}
            </footer>
          )}
        </div>
      </div>
    </Portal>
  );
}
