import { useEffect, useRef, useState, type ReactNode } from "react";
import { cx } from "../../lib/cx";

export interface DropdownItem {
  key: string;
  label: ReactNode;
  onSelect?: () => void;
  /** Render as a link instead of a button (still keyboard-activatable). */
  href?: string;
  danger?: boolean;
  disabled?: boolean;
}

export interface DropdownProps {
  /** The trigger. Receives open state so it can rotate a chevron, etc. */
  trigger: (props: { open: boolean }) => ReactNode;
  items: DropdownItem[];
  /** Accessible name for the menu. */
  label: string;
  align?: "left" | "right";
  className?: string;
}

/**
 * Accessible menu-button dropdown (WAI-ARIA menu pattern). The trigger exposes
 * `aria-haspopup="menu"` + `aria-expanded`; the panel is `role="menu"` with
 * `role="menuitem"` children. Keyboard: ArrowDown/Up move between items, Enter/Space
 * activate, Escape closes and returns focus to the trigger, and a click outside
 * dismisses. Used by the user menu and the org/legal-entity switchers so all three
 * behave identically.
 */
export function Dropdown({ trigger, items, label, align = "right", className = "" }: DropdownProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const itemRefs = useRef<(HTMLElement | null)[]>([]);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    }
    function onDocKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    }
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onDocKey, true);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onDocKey, true);
    };
  }, [open]);

  // Focus the first ENABLED item when the menu opens (the first item may be a
  // non-interactive header, e.g. the account email in the user menu).
  useEffect(() => {
    if (!open) return;
    const firstEnabled = items.findIndex((it) => !it.disabled);
    itemRefs.current[firstEnabled >= 0 ? firstEnabled : 0]?.focus();
  }, [open, items]);

  function close(focusTrigger = true) {
    setOpen(false);
    if (focusTrigger) triggerRef.current?.focus();
  }

  function move(from: number, delta: number) {
    const n = items.length;
    for (let i = 1; i <= n; i++) {
      const idx = (from + delta * i + n) % n;
      if (!items[idx].disabled) {
        itemRefs.current[idx]?.focus();
        return;
      }
    }
  }

  function onItemKeyDown(e: React.KeyboardEvent, i: number) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      move(i, 1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      move(i, -1);
    } else if (e.key === "Escape") {
      e.preventDefault();
      close();
    } else if (e.key === "Home") {
      e.preventDefault();
      move(-1, 1);
    } else if (e.key === "End") {
      e.preventDefault();
      move(0, -1);
    }
  }

  return (
    <div ref={rootRef} className={cx("relative", className)}>
      <button
        ref={triggerRef}
        type="button"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => {
          if ((e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") && !open) {
            e.preventDefault();
            setOpen(true);
          }
        }}
        className="rounded-lg focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-brand-300"
      >
        {trigger({ open })}
      </button>

      {open && (
        <div
          role="menu"
          aria-label={label}
          className={cx(
            "iq-pop-in absolute z-40 mt-1 min-w-48 overflow-hidden rounded-xl border border-slate-200 bg-white py-1 shadow-lg",
            align === "right" ? "right-0" : "left-0",
          )}
        >
          {items.map((item, i) => {
            const cls = cx(
              "flex w-full items-center gap-2 px-3 py-2 text-left text-sm focus-visible:outline-hidden",
              item.disabled
                ? "cursor-not-allowed text-slate-300"
                : item.danger
                  ? "text-rose-600 hover:bg-rose-50 focus:bg-rose-50"
                  : "text-slate-700 hover:bg-slate-50 focus:bg-slate-50",
            );
            const common = {
              role: "menuitem" as const,
              tabIndex: -1,
              ref: (el: HTMLElement | null) => {
                itemRefs.current[i] = el;
              },
              onKeyDown: (e: React.KeyboardEvent) => onItemKeyDown(e, i),
              className: cls,
            };
            if (item.href && !item.disabled) {
              return (
                <a key={item.key} href={item.href} onClick={() => close(false)} {...common}>
                  {item.label}
                </a>
              );
            }
            return (
              <button
                key={item.key}
                type="button"
                disabled={item.disabled}
                onClick={() => {
                  item.onSelect?.();
                  close();
                }}
                {...common}
              >
                {item.label}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
