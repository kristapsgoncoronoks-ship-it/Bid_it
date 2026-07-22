import { Suspense, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { AppShell } from "../components/shell/AppShell";
import { NAV_FLAT } from "../components/shell/nav";
import { DevBanner } from "./DevBanner";
import { FIXTURE_ORGS, FIXTURE_ENTITIES, FIXTURE_USER } from "./fixtures";
import type { Crumb } from "../components/ui";

/**
 * Hosts the `/design` showcase inside the real `AppShell`, driven entirely by
 * fixtures. Org/entity switching and search are wired to local state (no network),
 * proving the shell's interactions without a backend. Breadcrumbs are derived from
 * the active route's nav entry. This is the harness the visual-regression and smoke
 * tests target because it is fully deterministic.
 */
export function DesignLayout() {
  const [orgId, setOrgId] = useState(FIXTURE_ORGS[0].id);
  const [entityId, setEntityId] = useState(FIXTURE_ENTITIES[0].id);
  const [search, setSearch] = useState("");
  const { pathname } = useLocation();

  const current = NAV_FLAT.find((n) => n.to === pathname);
  const crumbs: Crumb[] = [
    { label: "Workspace", to: "/design" },
    ...(current && current.to !== "/design" ? [{ label: current.label }] : [{ label: "Dashboard" }]),
  ];

  return (
    <AppShell
      orgs={FIXTURE_ORGS}
      currentOrgId={orgId}
      onSwitchOrg={setOrgId}
      entities={FIXTURE_ENTITIES}
      currentEntityId={entityId}
      onSwitchEntity={setEntityId}
      user={FIXTURE_USER}
      onSignOut={() => window.alert("Sign out (fixture — no-op)")}
      search={search}
      onSearch={setSearch}
      breadcrumbs={crumbs}
      banner={<DevBanner />}
    >
      <Suspense fallback={<div className="py-10 text-sm text-slate-400">Loading…</div>}>
        <Outlet />
      </Suspense>
    </AppShell>
  );
}
