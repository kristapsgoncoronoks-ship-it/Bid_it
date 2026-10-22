import { useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { cx } from "../../lib/cx";

export interface TabItem {
  /** Stable value used for selection + the panel id. */
  value: string;
  label: ReactNode;
  /** Optional trailing count/badge in the tab. */
  badge?: ReactNode;
  disabled?: boolean;
}

export interface TabsProps {
  tabs: TabItem[];
  value: string;
  onChange: (value: string) => void;
  /** sr-only label describing what the tabs switch between. */
  label: string;
  /** Shared id prefix so tabs and their `TabPanel`s link via aria-controls /
   *  aria-labelledby. Pass the SAME value to `TabPanel`. Defaults to a generated id
   *  (fine when you don't render linked panels). */
  idBase?: string;
  className?: string;
}

function revealSelectedTab(container: HTMLDivElement | null) {
  const selected = container?.querySelector<HTMLButtonElement>('[role="tab"][aria-selected="true"]');
  if (!container || !selected) return;
  const area = container.getBoundingClientRect();
  const tab = selected.getBoundingClientRect();
  if (tab.left < area.left) container.scrollLeft -= Math.ceil(area.left - tab.left);
  else if (tab.right > area.right) container.scrollLeft += Math.ceil(tab.right - area.right);
}

/**
 * WAI-ARIA tablist. Controlled (`value` / `onChange`). Implements the keyboard
 * pattern non-negotiably: Left/Right (and Home/End) move between tabs with roving
 * tabindex, and only the active tab is in the tab order. Pair with `TabPanel` and
 * reference the same `idBase` so `aria-controls` / `aria-labelledby` line up.
 *
 * Note: this renders only the tab strip. Render the active panel yourself (a
 * simple `{value === "x" && <Panel/>}`), wrapped in `TabPanel` for the a11y wiring.
 */
export function Tabs({ tabs, value, onChange, label, idBase, className = "" }: TabsProps) {
  const generated = useId();
  const base = idBase ?? generated;
  const strip = useRef<HTMLDivElement>(null);
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const [hasOverflow, setHasOverflow] = useState(false);

  useLayoutEffect(() => {
    const element = strip.current;
    if (!element) return;
    const measure = () => {
      setHasOverflow(element.scrollWidth > element.clientWidth + 1);
      revealSelectedTab(element);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    for (const button of refs.current) if (button) observer.observe(button);
    return () => observer.disconnect();
  }, [tabs]);

  useLayoutEffect(() => {
    // This also covers selection changed by the parent, not only arrow keys.
    // Move only this strip: mounting a below-the-fold tablist must not scroll
    // the whole document to it before the user has chosen to go there.
    revealSelectedTab(strip.current);
  }, [value]);

  const enabledIndexes = tabs.map((t, i) => (t.disabled ? -1 : i)).filter((i) => i >= 0);

  function focusIndex(i: number) {
    const el = refs.current[i];
    if (el) {
      el.focus();
      onChange(tabs[i].value);
    }
  }

  function onKeyDown(e: React.KeyboardEvent, currentIndex: number) {
    const pos = enabledIndexes.indexOf(currentIndex);
    if (pos < 0) return;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") {
      e.preventDefault();
      focusIndex(enabledIndexes[(pos + 1) % enabledIndexes.length]);
    } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
      e.preventDefault();
      focusIndex(enabledIndexes[(pos - 1 + enabledIndexes.length) % enabledIndexes.length]);
    } else if (e.key === "Home") {
      e.preventDefault();
      focusIndex(enabledIndexes[0]);
    } else if (e.key === "End") {
      e.preventDefault();
      focusIndex(enabledIndexes[enabledIndexes.length - 1]);
    }
  }

  return (
    <div className="min-w-0 max-w-full">
      <div
        ref={strip}
        role="tablist"
        aria-label={label}
        aria-describedby={hasOverflow ? `${base}-scroll-hint` : undefined}
        className={cx("flex min-w-0 max-w-full gap-1 overflow-x-auto overscroll-x-contain border-b border-slate-200 pr-px pb-px", className)}
      >
        {tabs.map((t, i) => {
          const selected = t.value === value;
          return (
            <button
              key={t.value}
              ref={(el) => {
                refs.current[i] = el;
              }}
              role="tab"
              id={`${base}-tab-${t.value}`}
              aria-selected={selected}
              aria-controls={`${base}-panel-${t.value}`}
              tabIndex={selected ? 0 : -1}
              disabled={t.disabled}
              onClick={() => onChange(t.value)}
              onKeyDown={(e) => onKeyDown(e, i)}
              className={cx(
                "-mb-px inline-flex shrink-0 items-center gap-2 whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium transition",
                "focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-inset",
                selected
                  ? "border-brand-500 text-brand-700"
                  : "border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-700",
                t.disabled && "cursor-not-allowed opacity-40 hover:border-transparent hover:text-slate-500",
              )}
            >
              {t.label}
              {t.badge != null && (
                <span className="rounded-full bg-slate-100 px-1.5 py-0.5 text-xs text-slate-500">{t.badge}</span>
              )}
            </button>
          );
        })}
      </div>
      {hasOverflow && <p id={`${base}-scroll-hint`} className="mt-1 text-xs text-text-secondary">Scroll to see all sections.</p>}
    </div>
  );
}

/**
 * The panel a `Tabs` strip controls. Pass the SAME `idBase` you gave `Tabs` plus
 * the active tab's `value`, and the `aria-controls` / `aria-labelledby` wiring
 * lines up. Render only the active panel (`value === active && <TabPanel/>`).
 */
export function TabPanel({
  idBase,
  value,
  children,
}: {
  idBase: string;
  value: string;
  children: ReactNode;
}) {
  return (
    <div
      role="tabpanel"
      id={`${idBase}-panel-${value}`}
      aria-labelledby={`${idBase}-tab-${value}`}
      tabIndex={0}
      className="pt-4 focus-visible:outline-hidden"
    >
      {children}
    </div>
  );
}
