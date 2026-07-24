import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";

interface SessionRow {
  id: string;
  created_at: string;
  last_seen_at: string | null;
  user_agent: string | null;
  ip: string | null;
  current: boolean;
}

function when(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

export default function Sessions() {
  const qc = useQueryClient();
  const q = useQuery<SessionRow[]>({
    queryKey: ["sessions"],
    queryFn: async () => (await api.get("/auth/sessions")).data,
  });

  const revokeOne = useMutation({
    mutationFn: async (id: string) => api.delete(`/auth/sessions/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sessions"] }),
  });
  const revokeOthers = useMutation({
    mutationFn: async () => api.post("/auth/sessions/revoke-others"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sessions"] }),
  });

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Active sessions</h1>
          <p className="text-sm text-slate-500">Devices signed in to your account. Revoke any you don't recognise.</p>
        </div>
        <button
          className="btn-ghost text-sm"
          disabled={revokeOthers.isPending || (q.data?.length ?? 0) < 2}
          onClick={() => revokeOthers.mutate()}
        >
          {revokeOthers.isPending ? "Signing out…" : "Sign out everywhere else"}
        </button>
      </div>

      {/* Loading state */}
      {q.isLoading && <div className="card text-sm text-slate-400">Loading sessions…</div>}

      {/* Error state */}
      {q.isError && (
        <div className="card text-sm text-rose-600">Could not load your sessions. Try again.</div>
      )}

      {/* Empty state (shouldn't happen while signed in, but handled) */}
      {q.data && q.data.length === 0 && (
        <div className="card text-sm text-slate-500">No active sessions.</div>
      )}

      {q.data && q.data.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-slate-200">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
              <tr>
                <th className="px-3 py-2">Device</th>
                <th className="px-3 py-2">IP</th>
                <th className="px-3 py-2">Last active</th>
                <th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {q.data.map((s) => (
                <tr key={s.id} className="border-t border-slate-100">
                  <td className="px-3 py-2">
                    <span className="text-slate-700">{s.user_agent || "Unknown device"}</span>
                    {s.current && (
                      <span className="ml-2 rounded bg-emerald-50 px-1.5 py-0.5 text-xs font-medium text-emerald-700">
                        This device
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-slate-500">{s.ip || "—"}</td>
                  <td className="px-3 py-2 text-slate-500">{when(s.last_seen_at ?? s.created_at)}</td>
                  <td className="px-3 py-2 text-right">
                    {!s.current && (
                      <button
                        className="text-xs text-rose-600 hover:underline"
                        disabled={revokeOne.isPending}
                        onClick={() => revokeOne.mutate(s.id)}
                      >
                        Revoke
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
