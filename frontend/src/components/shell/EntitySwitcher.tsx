import { Dropdown, type DropdownItem } from "./Dropdown";
import { cx } from "../../lib/cx";

export interface SwitcherOption {
  id: string;
  name: string;
  /** Optional second line, e.g. a VAT number or plan. */
  sub?: string;
}

/**
 * A generic top-bar switcher used for BOTH the organization and the legal-entity
 * pickers — the two are structurally identical (choose one of N scopes) but
 * semantically distinct, so this is parameterised by `kind`. The current scope is
 * shown in the trigger; the menu lists the alternatives with the active one ticked.
 * When only one option exists it renders as static text (nothing to switch to).
 */
export function ScopeSwitcher({
  kind,
  options,
  currentId,
  onSwitch,
}: {
  kind: "organization" | "legal entity";
  options: SwitcherOption[];
  currentId: string;
  onSwitch: (id: string) => void;
}) {
  const current = options.find((o) => o.id === currentId) ?? options[0];
  const eyebrow = kind === "organization" ? "Organization" : "Legal entity";

  if (options.length <= 1) {
    return (
      <div className="px-2">
        <span className="block text-[10px] font-medium uppercase tracking-wide text-slate-400">{eyebrow}</span>
        <span className="block text-sm font-medium text-slate-700">{current?.name ?? "—"}</span>
      </div>
    );
  }

  const items: DropdownItem[] = options.map((o) => ({
    key: o.id,
    onSelect: () => onSwitch(o.id),
    label: (
      <span className="flex w-full items-center justify-between gap-3">
        <span className="min-w-0">
          <span className="block truncate font-medium">{o.name}</span>
          {o.sub && <span className="block truncate text-xs text-slate-400">{o.sub}</span>}
        </span>
        {o.id === currentId && (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true" className="shrink-0 text-brand-600">
            <path d="M5 12l5 5 9-11" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        )}
      </span>
    ),
  }));

  return (
    <Dropdown
      label={`Switch ${kind}`}
      align="left"
      trigger={({ open }) => (
        <span className={cx("flex items-center gap-2 rounded-lg px-2 py-1 hover:bg-slate-100")}>
          <span className="text-left">
            <span className="block text-[10px] font-medium uppercase tracking-wide text-slate-400">{eyebrow}</span>
            <span className="block max-w-[10rem] truncate text-sm font-medium text-slate-700">{current?.name}</span>
          </span>
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            aria-hidden="true"
            className={`text-slate-400 transition-transform ${open ? "rotate-180" : ""}`}
          >
            <path d="M6 9l6 6 6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
      )}
      items={items}
    />
  );
}
