import { useState, type ReactNode } from "react";
import { NavLink } from "react-router-dom";
import type { NavGroup } from "./nav";
import { ScopeSwitcher, type SwitcherOption } from "./EntitySwitcher";
import { UserMenu, type ShellUser } from "./UserMenu";
import type { DropdownItem } from "./Dropdown";
import { Drawer } from "../ui/Drawer";
import { SearchInput } from "../ui/SearchInput";
import { Breadcrumbs, type Crumb } from "../ui/Breadcrumbs";
import { cx } from "../../lib/cx";

export interface AppShellProps {
  /** The grouped nav to render. Caller-supplied so the shell has no built-in
   * opinion about destinations — `/design` passes its fixture IA, the live app
   * passes the real one (`frontend/src/lib/nav.ts`), already permission/module
   * filtered by the caller (the shell renders whatever it is given). */
  navGroups: NavGroup[];
  orgs: SwitcherOption[];
  currentOrgId: string;
  onSwitchOrg: (id: string) => void;
  /** Legal-entity switcher — omit when no such concept exists for the caller
   * (e.g. the live app today has no legal-entity model at all: wiring this with
   * invented data would be placeholder UI, not a feature). */
  entities?: SwitcherOption[];
  currentEntityId?: string;
  onSwitchEntity?: (id: string) => void;
  user: ShellUser;
  onSignOut: () => void;
  /** Global search box — omit when no search backend exists for the caller. */
  search?: string;
  onSearch?: (v: string) => void;
  breadcrumbs?: Crumb[];
  /** Full-width notice under the top bar (e.g. the dev-fixtures banner, or a
   * suspended-workspace notice). */
  banner?: ReactNode;
  /** Extra items appended to the user menu, before Sign out (e.g. "Sessions"). */
  userMenuExtraItems?: DropdownItem[];
  /** Where "Account settings" in the user menu points. */
  accountHref?: string;
  children: ReactNode;
}

function NavList({ navGroups, onNavigate }: { navGroups: NavGroup[]; onNavigate?: () => void }) {
  return (
    <nav aria-label="Primary" className="flex flex-col gap-5 px-3 py-4">
      {navGroups.map((group) => (
        <div key={group.title}>
          <p className="px-3 pb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-400">{group.title}</p>
          <ul className="flex flex-col gap-0.5">
            {group.items.map((item) => (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  end={item.end ?? false}
                  onClick={onNavigate}
                  className={({ isActive }) =>
                    cx(
                      "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition",
                      "focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-brand-300",
                      isActive ? "bg-brand-50 text-brand-700" : "text-slate-600 hover:bg-slate-100 hover:text-slate-800",
                    )
                  }
                >
                  <span className="shrink-0 text-current opacity-80">{item.icon}</span>
                  {item.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  );
}

function Brand() {
  return (
    <div className="flex items-center gap-2 px-4 py-4">
      <div className="grid h-8 w-8 place-items-center rounded-lg bg-brand-500 text-sm font-bold text-white">iQ</div>
      <span className="text-lg font-semibold tracking-tight text-slate-800">InvoiceIQ</span>
    </div>
  );
}

/**
 * The application shell: a persistent grouped sidebar + a sticky top bar wrapping
 * the routed page content. Responsive — on desktop the sidebar is fixed at 16rem;
 * below `lg` it collapses behind a hamburger that opens the same nav in a `Drawer`.
 * The top bar carries the org switcher (+ an optional legal-entity switcher and an
 * optional global search box, when the caller has something real to back them
 * with), and the user menu. A skip link jumps keyboard users straight to `<main>`.
 *
 * `navGroups` is caller-supplied and pre-filtered — this component has no opinion
 * about destinations, permissions or module gating; both the `/design` showcase
 * (`DesignLayout`) and the live app (`Layout`) mount it with their own nav data.
 */
export function AppShell({
  navGroups,
  orgs, currentOrgId, onSwitchOrg,
  entities, currentEntityId, onSwitchEntity,
  user, onSignOut, search, onSearch, breadcrumbs, banner,
  userMenuExtraItems, accountHref,
  children,
}: AppShellProps) {
  const [mobileNav, setMobileNav] = useState(false);

  const switchers = (
    <div className="flex flex-col gap-2 border-y border-slate-100 px-3 py-3">
      <ScopeSwitcher kind="organization" options={orgs} currentId={currentOrgId} onSwitch={onSwitchOrg} />
      {entities && entities.length > 0 && (
        <ScopeSwitcher
          kind="legal entity"
          options={entities}
          currentId={currentEntityId ?? entities[0].id}
          onSwitch={onSwitchEntity ?? (() => {})}
        />
      )}
    </div>
  );

  return (
    <div className="min-h-screen bg-slate-50 text-slate-800">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:rounded-lg focus:bg-brand-600 focus:px-3 focus:py-2 focus:text-sm focus:text-white"
      >
        Skip to content
      </a>

      {/* Desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 hidden w-64 flex-col border-r border-slate-200 bg-white lg:flex">
        <Brand />
        {switchers}
        <div className="flex-1 overflow-y-auto">
          <NavList navGroups={navGroups} />
        </div>
      </aside>

      {/* Mobile nav drawer */}
      <Drawer open={mobileNav} onClose={() => setMobileNav(false)} title="Menu" side="left" size="sm">
        {switchers}
        <NavList navGroups={navGroups} onNavigate={() => setMobileNav(false)} />
      </Drawer>

      <div className="lg:pl-64">
        {/* Top bar */}
        <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/90 backdrop-blur-sm">
          <div className="flex items-center gap-3 px-4 py-2.5">
            <button
              type="button"
              className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-brand-300 lg:hidden"
              aria-label="Open navigation menu"
              aria-expanded={mobileNav}
              onClick={() => setMobileNav(true)}
            >
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M4 6h16M4 12h16M4 18h16" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
              </svg>
            </button>

            <div className="min-w-0 flex-1">
              {breadcrumbs && breadcrumbs.length > 0 && <Breadcrumbs items={breadcrumbs} />}
            </div>

            {onSearch && (
              <SearchInput
                value={search ?? ""}
                onChange={onSearch}
                label="Search the workspace"
                placeholder="Search…"
                className="hidden w-64 md:block"
              />
            )}
            <UserMenu user={user} onSignOut={onSignOut} extraItems={userMenuExtraItems} accountHref={accountHref} />
          </div>
        </header>

        {banner}

        <main id="main" tabIndex={-1} className="mx-auto max-w-6xl px-4 py-6 focus:outline-hidden sm:px-6">
          {children}
        </main>
      </div>
    </div>
  );
}
