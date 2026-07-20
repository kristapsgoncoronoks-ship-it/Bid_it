import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { api, apiError } from "../lib/api";
import { shortDate } from "../lib/format";
import { ASSIGNABLE_ROLES, ROLE_LABELS, isSysadmin } from "../lib/roles";
import type { Invite, Member, UserRoleName } from "../lib/types";

export default function Team() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const canManage = isSysadmin(user);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<UserRoleName>("user");
  const [error, setError] = useState<string | null>(null);

  const members = useQuery<Member[]>({ queryKey: ["team", "members"], queryFn: async () => (await api.get("/team/members")).data });
  const invites = useQuery<Invite[]>({
    queryKey: ["team", "invites"],
    queryFn: async () => (await api.get("/team/invites")).data,
    enabled: canManage,
  });

  const invite = useMutation({
    mutationFn: async () => (await api.post("/team/invites", { email, role })).data,
    onSuccess: () => {
      setEmail("");
      setError(null);
      qc.invalidateQueries({ queryKey: ["team", "invites"] });
    },
    onError: (e) => setError(apiError(e)),
  });
  const revoke = useMutation({
    mutationFn: async (id: string) => api.delete(`/team/invites/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["team", "invites"] }),
  });
  const patch = useMutation({
    mutationFn: async (v: { id: string; body: Partial<Member> }) => (await api.patch(`/team/members/${v.id}`, v.body)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["team", "members"] }),
    onError: (e) => setError(apiError(e)),
  });

  const inviteLink = (t: string) => `${location.origin}/accept-invite?token=${t}`;

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Team</h1>
        <p className="text-sm text-slate-500">People in this workspace. Invites share a link — no email required.</p>
      </div>

      {error && <div className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-600">{error}</div>}

      {canManage && (
        <div className="card space-y-3">
          <h2 className="text-sm font-semibold text-slate-600">Invite a member</h2>
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex-1">
              <label className="label">Email</label>
              <input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@company.com" />
            </div>
            <div>
              <label className="label">Role</label>
              <select className="input w-36" value={role} onChange={(e) => setRole(e.target.value as UserRoleName)}>
                {ASSIGNABLE_ROLES.map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
              </select>
            </div>
            <button className="btn-primary" disabled={invite.isPending || !email} onClick={() => invite.mutate()}>
              {invite.isPending ? "Inviting…" : "Create invite"}
            </button>
          </div>

          {(invites.data ?? []).length > 0 && (
            <div className="space-y-2 border-t border-slate-100 pt-3">
              <div className="text-xs font-medium uppercase text-slate-400">Pending invites</div>
              {invites.data!.map((iv) => (
                <div key={iv.id} className="flex flex-wrap items-center gap-2 rounded-lg bg-slate-50 px-3 py-2 text-sm">
                  <span className="font-medium">{iv.email}</span>
                  <span className="badge bg-slate-200 text-slate-600">{ROLE_LABELS[iv.role]}</span>
                  <input readOnly value={inviteLink(iv.token)} className="input flex-1 min-w-[200px] text-xs" onFocus={(e) => e.target.select()} />
                  <button className="text-brand-600 hover:underline" onClick={() => navigator.clipboard?.writeText(inviteLink(iv.token))}>copy</button>
                  <button className="text-rose-500 hover:underline" onClick={() => revoke.mutate(iv.id)}>revoke</button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="card overflow-x-auto p-0">
        <table className="w-full text-sm">
          <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-3">Member</th>
              <th className="px-4 py-3">Role</th>
              <th className="px-4 py-3">Joined</th>
              <th className="px-4 py-3">Status</th>
              {canManage && <th className="px-4 py-3"></th>}
            </tr>
          </thead>
          <tbody>
            {members.data?.map((m) => (
              <tr key={m.id} className="border-b border-slate-100 last:border-0">
                <td className="px-4 py-3">
                  <div className="font-medium">{m.name}</div>
                  <div className="text-xs text-slate-400">{m.email}</div>
                </td>
                <td className="px-4 py-3">
                  {canManage && m.id !== user?.id ? (
                    <select className="input w-32 py-1 text-xs" value={m.role} onChange={(e) => patch.mutate({ id: m.id, body: { role: e.target.value as UserRoleName } })}>
                      {ASSIGNABLE_ROLES.map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
                    </select>
                  ) : (
                    <span className="badge bg-slate-100 text-slate-600">{ROLE_LABELS[m.role]}</span>
                  )}
                </td>
                <td className="px-4 py-3 text-slate-500">{shortDate(m.created_at)}</td>
                <td className="px-4 py-3">
                  <span className={`badge ${m.is_active ? "bg-emerald-100 text-emerald-700" : "bg-slate-100 text-slate-500"}`}>
                    {m.is_active ? "active" : "disabled"}
                  </span>
                </td>
                {canManage && (
                  <td className="px-4 py-3 text-right">
                    {m.id !== user?.id && (
                      <button className="text-slate-500 hover:underline" onClick={() => patch.mutate({ id: m.id, body: { is_active: !m.is_active } })}>
                        {m.is_active ? "disable" : "enable"}
                      </button>
                    )}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
