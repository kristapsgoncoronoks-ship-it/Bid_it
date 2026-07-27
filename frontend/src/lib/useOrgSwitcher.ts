import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type { SwitcherOption } from "../components/shell/EntitySwitcher";

interface OrgRow {
  org_id: string;
  name: string;
  role: string;
  current: boolean;
}

/**
 * Drives the shell's org `ScopeSwitcher` from `GET /auth/organizations` and
 * `POST /auth/switch-org/{id}`. Replaces the old bespoke `OrgSwitcher.tsx` (which
 * rendered the same "static text when there's only one org, else a picker" logic
 * ScopeSwitcher already provides generically) — this hook only supplies the data
 * and the switch action.
 */
export function useOrgSwitcher(): {
  options: SwitcherOption[];
  currentId: string;
  onSwitch: (id: string) => void;
} {
  const { data } = useQuery<OrgRow[]>({
    queryKey: ["my-orgs"],
    queryFn: async () => (await api.get("/auth/organizations")).data,
  });
  const orgs = data ?? [];
  const current = orgs.find((o) => o.current) ?? orgs[0];

  async function onSwitch(id: string) {
    if (!id || id === current?.org_id) return;
    await api.post(`/auth/switch-org/${id}`);
    // Full reload: AuthContext refetches /auth/me and every query re-scopes to
    // the new org, so no stale cross-org data lingers.
    location.assign("/");
  }

  return {
    options: orgs.map((o) => ({ id: o.org_id, name: o.name })),
    currentId: current?.org_id ?? "",
    onSwitch,
  };
}
