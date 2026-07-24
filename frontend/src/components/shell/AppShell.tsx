import { useState, type ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { NAV_GROUPS } from "./nav";
import { ScopeSwitcher, type SwitcherOption } from "./EntitySwitcher";
import { UserMenu, type ShellUser } from "./UserMenu";
import { Drawer } from "../ui/Drawer";
import { SearchInput } from "../ui/SearchInput";
import { Breadcrumbs, type Crumb } from "../ui/Breadcrumbs";
import { cx } from "../../lib/cx";

export interface AppShellProps {
  orgs: SwitcherOption[];
  currentOrgId: string;
  onSwitchOrg: (id: string) => void;
  entities: SwitcherOption[];
  currentEntityId: string;
  onSwitchEntity: (id: string) => void;
  user: ShellUser;
  onSignOut: () => void;
  search: string;
  onSearch: (v: string) => void;
  breadcrumbs?: Crumb[];
  /** Full-width notice under the top bar (e.g. the dev-fixtures banner). */
  banner?: ReactNode;
  children: ReactNode;
}

function NavList({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav aria-label="Primary" className="flex flex-col gap-5 px-3 py-4">
      {NAV_GROUPS.map((group) => (
        <div key={group.title}>
          <p className="px-3 pb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-400">{group.title}</p>
          <ul className="flex flex-col gap-0.5">
            {group.items.map((item) => (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  end={item.to === "/design"}
                  onClick={onNavigate}
                  className={({ isActive }) =>
                    cx(
                      "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition",
                      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-300",
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
 * The top bar carries the org + legal-entity switchers, global search, and the user
 * menu. A skip link jumps keyboard users straight to `<main>`.
 */
export function AppShell({
  orgs, currentOrgId, onSwitchOrg,
  entities, currentEntityId, onSwitchEntity,
  user, onSignOut, search, onSearch, breadcrumbs, banner, children,
}: AppShellProps) {
  const [mobileNav, setMobileNav] = useState(false);

  const switchers = (
    <div className="flex flex-col gap-2 border-y border-slate-100 px-3 py-3">
      <ScopeSwitcher kind="organization" options={orgs} currentId={currentOrgId} onSwitch={onSwitchOrg} />
      <ScopeSwitcher kind="legal entity" options={entities} currentId={currentEntityId} onSwitch={onSwitchEntity} />
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
          <NavList />
        </div>
      </aside>

      {/* Mobile nav drawer */}
      <Drawer open={mobileNav} onClose={() => setMobileNav(false)} title="Menu" side="left" size="sm">
        {switchers}
        <NavList onNavigate={() => setMobileNav(false)} />
      </Drawer>

      <div className="lg:pl-64">
        {/* Top bar */}
        <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/90 backdrop-blur">
          <div className="flex items-center gap-3 px-4 py-2.5">
            <button
              type="button"
              className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 lg:hidden"
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

            <SearchInput
              value={search}
              onChange={onSearch}
              label="Search the workspace"
              placeholder="Search…"
              className="hidden w-64 md:block"
            />
            <UserMenu user={user} onSignOut={onSignOut} />
          </div>
        </header>

        {banner}

        <main id="main" tabIndex={-1} className="mx-auto max-w-6xl px-4 py-6 focus:outline-none sm:px-6">
          {children}
        </main>
      </div>
    </div>
  );
}
