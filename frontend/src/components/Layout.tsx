import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { hasVatPerm, isAdminOrAbove, isOwner, type VatPermission } from "../lib/roles";
import { useModules } from "../lib/useModules";
import { useOrgSwitcher } from "../lib/useOrgSwitcher";
import { LIVE_NAV, matchNavItem, type LiveNavGroup, type LiveNavItem } from "../lib/nav";
import { AppShell } from "./shell/AppShell";
import type { NavGroup } from "./shell/nav";
import { icon } from "./shell/nav";
import type { Crumb } from "./ui/Breadcrumbs";

/**
 * The application shell for the live app (board I1.2) — mounts the same `AppShell`
 * the `/design` showcase uses (`docs/DESIGN_SYSTEM.md`), grouped Overview /
 * Payables / Receivables / Insights / Workspace instead of the flat ~28-item nav
 * this replaces. Filtering is unchanged in spirit from the old flat `NAV` array:
 * a `module` flag hides an item until that add-on is enabled, `admin`/`owner`
 * hide business-admin/owner-only items. This filtering is cosmetic UX only — the
 * server is the real permission boundary (master-context §6), unchanged by this.
 *
 * No legal-entity switcher and no search box are wired: neither concept exists in
 * the backend today (verified — no entity model, no search endpoint), and mounting
 * either with nothing behind it would be placeholder UI, not a feature.
 */
function filterNav(
  groups: LiveNavGroup[],
  opts: {
    isEnabled: (m: string) => boolean;
    admin: boolean;
    owner: boolean;
    hasPerm: (p: VatPermission) => boolean;
  },
): NavGroup[] {
  return groups
    .map((g) => ({
      title: g.title,
      items: g.items.filter(
        (i: LiveNavItem) =>
          (!i.module || opts.isEnabled(i.module)) &&
          (!i.admin || opts.admin) &&
          (!i.owner || opts.owner) &&
          (!i.perm || opts.hasPerm(i.perm)),
      ),
    }))
    .filter((g) => g.items.length > 0);
}

export function Layout() {
  const { user, org, logout } = useAuth();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const { isEnabled } = useModules();
  const orgSwitcher = useOrgSwitcher();

  const admin = isAdminOrAbove(user);
  const owner = isOwner(user);
  const navGroups = filterNav(LIVE_NAV, {
    isEnabled,
    admin,
    owner,
    hasPerm: (p) => hasVatPerm(user, p),
  });
  if (user?.is_platform_admin) {
    const workspace = navGroups.find((g) => g.title === "Workspace");
    const platformItem = { to: "/platform", label: "Platform", icon: icon("M4 4h16v16H4V4zm4 4h8v8H8V8z") };
    if (workspace) workspace.items.push(platformItem);
    else navGroups.push({ title: "Workspace", items: [platformItem] });
  }

  const current = matchNavItem(pathname);
  const crumbs: Crumb[] = current ? [{ label: current.label }] : [];

  const suspended = org?.status && org.status !== "active";

  return (
    <AppShell
      navGroups={navGroups}
      orgs={orgSwitcher.options}
      currentOrgId={orgSwitcher.currentId}
      onSwitchOrg={orgSwitcher.onSwitch}
      user={{ name: user?.name ?? user?.email ?? "—", email: user?.email ?? "", role: user?.role ?? "" }}
      onSignOut={async () => {
        await logout();
        navigate("/login");
      }}
      breadcrumbs={crumbs}
      userMenuExtraItems={[{ key: "sessions", label: "Sessions", href: "/sessions" }]}
      accountHref="/settings"
      banner={
        suspended ? (
          <div className="bg-rose-600 px-4 py-2 text-center text-sm font-medium text-white">
            This workspace is {org?.status}. Some actions are disabled — please contact support or update billing.
          </div>
        ) : undefined
      }
    >
      <Outlet />
    </AppShell>
  );
}
