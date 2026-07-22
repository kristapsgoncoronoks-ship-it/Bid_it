import { Dropdown, type DropdownItem } from "./Dropdown";

export interface ShellUser {
  name: string;
  email: string;
  role: string;
}

function initials(name: string): string {
  return name
    .split(" ")
    .map((p) => p[0])
    .filter(Boolean)
    .slice(0, 2)
    .join("")
    .toUpperCase();
}

/**
 * The account menu in the top bar: an avatar + name trigger opening a menu with
 * account links and sign-out. Fully keyboard-driven via `Dropdown` (arrow keys,
 * Escape, focus return). Sign-out is styled as a danger item.
 */
export function UserMenu({
  user,
  onSignOut,
  extraItems = [],
}: {
  user: ShellUser;
  onSignOut: () => void;
  extraItems?: DropdownItem[];
}) {
  const items: DropdownItem[] = [
    { key: "email", label: <span className="text-xs text-slate-400">{user.email}</span>, disabled: true },
    { key: "account", label: "Account settings", href: "/design/settings" },
    ...extraItems,
    { key: "signout", label: "Sign out", danger: true, onSelect: onSignOut },
  ];

  return (
    <Dropdown
      label="Account menu"
      align="right"
      trigger={({ open }) => (
        <span className="flex items-center gap-2 rounded-lg px-1.5 py-1 hover:bg-slate-100">
          <span className="grid h-8 w-8 place-items-center rounded-full bg-brand-100 text-xs font-semibold text-brand-700">
            {initials(user.name)}
          </span>
          <span className="hidden text-left sm:block">
            <span className="block text-sm font-medium leading-tight text-slate-700">{user.name}</span>
            <span className="block text-xs capitalize leading-tight text-slate-400">{user.role}</span>
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
