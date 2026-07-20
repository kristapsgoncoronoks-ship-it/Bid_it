import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Navigate } from "react-router-dom";
import { useToast } from "../components/Toast";
import { useAuth } from "../auth/AuthContext";
import { api, apiError } from "../lib/api";
import { shortDate } from "../lib/format";
import type { Tenant } from "../lib/types";

const PLANS = ["trial", "starter", "pro", "enterprise"];
const STATUSES = ["active", "suspended", "canceled"];

export default function Platform() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();

  const tenants = useQuery<Tenant[]>({
    queryKey: ["platform", "tenants"],
    queryFn: async () => (await api.get("/platform/tenants")).data,
    enabled: !!user?.is_platform_admin,
  });

  const patch = useMutation({
    mutationFn: async (v: { id: string; body: Partial<Tenant> }) => (await api.patch(`/platform/tenants/${v.id}`, v.body)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["platform", "tenants"] }),
    onError: (e) => toast.error(apiError(e)),
  });

  if (user && !user.is_platform_admin) return <Navigate to="/" replace />;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Platform · Tenants</h1>
        <p className="text-sm text-slate-500">Operator view. Tenant metadata only — never their invoice data.</p>
      </div>

      <div className="card overflow-x-auto p-0">
        <table className="w-full text-sm">
          <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-3">Tenant</th>
              <th className="px-4 py-3">Created</th>
              <th className="px-4 py-3 text-right">Seats</th>
              <th className="px-4 py-3">Plan</th>
              <th className="px-4 py-3">Status</th>
            </tr>
          </thead>
          <tbody>
            {tenants.data?.map((t) => (
              <tr key={t.id} className="border-b border-slate-100 last:border-0">
                <td className="px-4 py-3 font-medium">{t.name}</td>
                <td className="px-4 py-3 text-slate-500">{shortDate(t.created_at)}</td>
                <td className="px-4 py-3 text-right text-slate-500">{t.seats_used}</td>
                <td className="px-4 py-3">
                  <select className="input w-32 py-1 text-xs" value={t.plan} onChange={(e) => patch.mutate({ id: t.id, body: { plan: e.target.value } })}>
                    {PLANS.map((p) => <option key={p} value={p}>{p}</option>)}
                  </select>
                </td>
                <td className="px-4 py-3">
                  <select
                    className={`input w-32 py-1 text-xs ${t.status !== "active" ? "text-rose-600" : ""}`}
                    value={t.status}
                    onChange={(e) => patch.mutate({ id: t.id, body: { status: e.target.value } })}
                  >
                    {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
