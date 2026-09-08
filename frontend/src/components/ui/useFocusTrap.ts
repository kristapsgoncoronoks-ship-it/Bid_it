import { useEffect, useRef, type RefObject } from "react";

const FOCUSABLE =
  'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),' +
  'select:not([disabled]),[tabindex]:not([tabindex="-1"])';

/**
 * Focus management for modal surfaces (dialogs, drawers). While `active`:
 *
 * - moves focus into the container on open (`initialFocus` when given, else the
 *   first focusable, else the container),
 * - traps Tab / Shift+Tab within the container (wraps at the ends),
 * - calls `onEscape` when Escape is pressed,
 * - restores focus to the previously-focused element on close.
 *
 * Returns a ref to attach to the surface container. This is the single place the
 * kit implements the WAI-ARIA "keep focus inside the dialog" contract, so every
 * overlay inherits it identically.
 */
export function useFocusTrap<T extends HTMLElement>(
  active: boolean,
  onEscape?: () => void,
  initialFocus?: RefObject<HTMLElement | null>,
) {
  const ref = useRef<T>(null);

  // FE-022 (reference R4 review, Adversarial A-1): the trap effect used to
  // depend on `onEscape` as well as `active`. Every caller passes an inline
  // closure, so every re-render of the parent — every keystroke into an input
  // whose state the parent holds — re-ran the effect: the cleanup handed focus
  // back to the pre-open element and the effect moved it to the first
  // focusable again (the header's Close button). Typing into any Modal form
  // lost focus after one character. The latest callback is read through a ref
  // so the effect depends on `active` alone and focus is placed exactly once,
  // on open.
  const onEscapeRef = useRef(onEscape);
  useEffect(() => {
    onEscapeRef.current = onEscape;
  }, [onEscape]);

  useEffect(() => {
    if (!active) return;
    const container = ref.current;
    if (!container) return;

    const previouslyFocused = document.activeElement as HTMLElement | null;

    const focusables = () =>
      Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (el) => el.offsetParent !== null || el === document.activeElement,
      );

    // Move focus in on open. A surface whose purpose is typing (the go-to-page
    // palette) names its input; otherwise the first focusable.
    const preferred = initialFocus?.current;
    const first = preferred && container.contains(preferred) ? preferred : focusables()[0];
    (first ?? container).focus();

    // Escape is handled at the document level (capture) so it fires no matter
    // where focus currently sits — robust against focus briefly leaving the trap.
    const onDocKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        e.preventDefault();
        onEscapeRef.current?.();
        return;
      }
      if (e.key !== "Tab") return;
      // Only trap Tab while focus is (or should be) inside the container.
      const items = focusables();
      if (items.length === 0) {
        e.preventDefault();
        container.focus();
        return;
      }
      const firstEl = items[0];
      const lastEl = items[items.length - 1];
      const activeEl = document.activeElement;
      const inside = container.contains(activeEl);
      if (!inside) {
        e.preventDefault();
        firstEl.focus();
      } else if (e.shiftKey && activeEl === firstEl) {
        e.preventDefault();
        lastEl.focus();
      } else if (!e.shiftKey && activeEl === lastEl) {
        e.preventDefault();
        firstEl.focus();
      }
    };

    document.addEventListener("keydown", onDocKeyDown, true);
    return () => {
      document.removeEventListener("keydown", onDocKeyDown, true);
      // Restore focus to where the user was before the overlay opened.
      previouslyFocused?.focus?.();
    };
    // `initialFocus` is a ref object: stable for the component's life, read at
    // activation time — not a dependency (see the `onEscape` note above).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  return ref;
}
