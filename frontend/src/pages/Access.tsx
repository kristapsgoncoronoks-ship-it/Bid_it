import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { api, apiError } from "../lib/api";
import { isSysadmin } from "../lib/roles";
import type { RolePolicy, Usage } from "../lib/types";

export default function Access() {
  const { user } = useAuth();
  const canEdit = isSysadmin(user);

  const matrix = useQuery<RolePolicy[]>({
    queryKey: ["access", "matrix"],
    queryFn: async () => (await api.get("/access/matrix")).data,
  });
  const usage = useQuery<Usage>({
    queryKey: ["access", "usage"],
    queryFn: async () => (await api.get("/access/usage")).data,
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Access & limits matrix</h1>
        <p className="text-sm text-slate-500">
          The four user groups and their monthly usage limits. Free and paying users are capped here;
          admins and sysadmins are unlimited. <span className="text-slate-400">A limit of 0 means unlimited.</span>
        </p>
      </div>

      {usage.data && (
        <div className="card flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-sm font-medium text-slate-600">Your usage this month</div>
            <div className="text-xs text-slate-400">Access level: {usage.data.role}</div>
          </div>
          <div className="text-right">
            <div className="text-2xl font-semibold text-brand-600">
              {usage.data.invoices_used}
              <span className="text-base font-normal text-slate-400">
                {usage.data.unlimited ? " / ∞" : ` / ${usage.data.invoice_limit}`}
              </span>
            </div>
            <div className="text-xs text-slate-400">invoices created</div>
          </div>
        </div>
      )}

      {!canEdit && (
        <div className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-700">
          You can view the matrix, but only a sysadmin can change the limits.
        </div>
      )}

      <div className="card overflow-x-auto p-0">
        <table className="w-full text-sm">
          <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-3">User group</th>
              <th className="px-4 py-3 text-right">Invoices / month</th>
              <th className="px-4 py-3 text-right">Uploads / month</th>
              {canEdit && <th className="px-4 py-3"></th>}
            </tr>
          </thead>
          <tbody>
            {matrix.data?.map((p) => (
              <MatrixRow key={p.role} policy={p} canEdit={canEdit} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function MatrixRow({ policy, canEdit }: { policy: RolePolicy; canEdit: boolean }) {
  const qc = useQueryClient();
  const [inv, setInv] = useState(String(policy.monthly_invoice_limit));
  const [up, setUp] = useState(String(policy.monthly_upload_limit));
  useEffect(() => { setInv(String(policy.monthly_invoice_limit)); setUp(String(policy.monthly_upload_limit)); },
    [policy.monthly_invoice_limit, policy.monthly_upload_limit]);

  const dirty = inv !== String(policy.monthly_invoice_limit) || up !== String(policy.monthly_upload_limit);
  const save = useMutation({
    mutationFn: async () =>
      (await api.put(`/access/matrix/${policy.role}`, {
        monthly_invoice_limit: Number(inv) || 0,
        monthly_upload_limit: Number(up) || 0,
      })).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["access"] }),
    onError: (e) => alert(apiError(e)),
  });

  const limitLabel = (v: string) => (Number(v) === 0 ? "unlimited" : v);

  return (
    <tr className="border-b border-slate-100 last:border-0">
      <td className="px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="font-medium text-slate-700">{policy.label}</span>
          <span className={`badge ${policy.paid ? "bg-emerald-100 text-emerald-700" : "bg-slate-100 text-slate-500"}`}>
            {policy.paid ? "paid" : "free"}
          </span>
        </div>
        <div className="text-xs text-slate-400">{policy.description}</div>
      </td>
      <td className="px-4 py-3 text-right">
        {canEdit ? (
          <input className="input w-24 py-1 text-right" type="number" min="0" value={inv} onChange={(e) => setInv(e.target.value)} />
        ) : (
          <span className="text-slate-600">{limitLabel(inv)}</span>
        )}
      </td>
      <td className="px-4 py-3 text-right">
        {canEdit ? (
          <input className="input w-24 py-1 text-right" type="number" min="0" value={up} onChange={(e) => setUp(e.target.value)} />
        ) : (
          <span className="text-slate-600">{limitLabel(up)}</span>
        )}
      </td>
      {canEdit && (
        <td className="px-4 py-3 text-right">
          <button className="btn-primary py-1 text-xs" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? "Saving…" : "Save"}
          </button>
        </td>
      )}
    </tr>
  );
}
