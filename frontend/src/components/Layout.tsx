import { Navigate, Outlet, useLocation, useNavigate } from "react-router-dom";
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

  const suspended = Boolean(org?.status && org.status !== "active");

  // PROD-001 (audit 2026-09-05): a suspended workspace renders ONE destination.
  // Every data route answers 401 while the org is suspended, so the normal nav
  // would be a wall of dead links and silent empty tables. The owner (the only
  // role holding BILLING_MANAGE) is routed to Plan & billing, which the server
  // still serves for a suspended org; everyone else gets the reason and who can
  // act on it. Note the banner below used to be unreachable: the identity read
  // itself 401'd, so the shell never mounted for a suspended tenant.
  const billingItem = LIVE_NAV.flatMap((g) => g.items).find((i) => i.to === "/billing");
  const suspendedNav: NavGroup[] =
    suspended && owner && billingItem ? [{ title: "Workspace", items: [billingItem] }] : [];

  return (
    <AppShell
      navGroups={suspended ? suspendedNav : navGroups}
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
          <div role="alert" className="bg-rose-600 px-4 py-2 text-center text-sm font-medium text-white">
            This workspace is {org?.status}.{" "}
            {owner
              ? "Update the plan or payment details on Plan & billing to restore access, or contact support if you believe this is a mistake."
              : "Only the workspace owner can restore access — ask them to check Plan & billing."}
          </div>
        ) : undefined
      }
    >
      {!suspended ? (
        <Outlet />
      ) : owner ? (
        pathname === "/billing" ? <Outlet /> : <Navigate to="/billing" replace />
      ) : (
        <div className="mx-auto max-w-xl">
          <div className="card space-y-2">
            <h1 className="text-lg font-semibold">This workspace is suspended</h1>
            <p className="text-sm text-slate-600">
              Your account is fine; the workspace&apos;s subscription is not. Nothing here is lost —
              access returns as soon as the workspace owner updates the plan or payment details.
            </p>
            <p className="text-sm text-slate-500">
              Owner: {orgSwitcher.options.find((o) => o.id === orgSwitcher.currentId)?.name ?? org?.name}
              {" "}— reach them directly; support cannot change a workspace&apos;s billing on a member&apos;s request.
            </p>
          </div>
        </div>
      )}
    </AppShell>
  );
}
