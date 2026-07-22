import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

interface OrgRow {
  org_id: string;
  name: string;
  role: string;
  current: boolean;
}

/** Header control to switch the active organization. Renders as plain text when
 * the user belongs to a single org, and a picker when they belong to several. */
export function OrgSwitcher({ currentName }: { currentName?: string }) {
  const { data } = useQuery<OrgRow[]>({
    queryKey: ["my-orgs"],
    queryFn: async () => (await api.get("/auth/organizations")).data,
  });
  const orgs = data ?? [];

  if (orgs.length <= 1) {
    return <div className="font-medium text-slate-700">{currentName ?? orgs[0]?.name ?? "—"}</div>;
  }

  const current = orgs.find((o) => o.current);

  async function switchTo(id: string) {
    if (!id || id === current?.org_id) return;
    await api.post(`/auth/switch-org/${id}`);
    // Full reload: AuthContext refetches /auth/me and every query re-scopes to
    // the new org, so no stale cross-org data lingers.
    location.assign("/");
  }

  return (
    <select
      aria-label="Active organization"
      className="rounded border border-slate-200 bg-white px-2 py-1 text-sm font-medium text-slate-700"
      value={current?.org_id ?? ""}
      onChange={(e) => switchTo(e.target.value)}
    >
      {orgs.map((o) => (
        <option key={o.org_id} value={o.org_id}>
          {o.name}
        </option>
      ))}
    </select>
  );
}
