import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import axios from "axios";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, apiError, tokenStore } from "../lib/api";
import type { InvitePreview } from "../lib/types";

export default function AcceptInvite() {
  const [params] = useSearchParams();
  const token = params.get("token") || "";
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const preview = useQuery<InvitePreview>({
    queryKey: ["invite", token],
    queryFn: async () => (await api.get(`/auth/invite/${token}`)).data,
    enabled: !!token,
    retry: false,
  });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api.post("/auth/accept-invite", { token, name, password });
      tokenStore.set(r.data.token.access_token);
      location.assign("/"); // full reload so AuthContext picks up the session
    } catch (err) {
      setError(apiError(err));
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center justify-center gap-2">
          <div className="grid h-9 w-9 place-items-center rounded-lg bg-brand-500 text-sm font-bold text-white">iQ</div>
          <span className="text-xl font-semibold tracking-tight">InvoiceIQ</span>
        </div>
        <div className="card">
          {!token ? (
            <div className="text-sm text-rose-600">This invitation link is incomplete.</div>
          ) : preview.isLoading ? (
            <div className="flex items-center gap-3 text-sm text-slate-500">
              <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-brand-500" />
              Checking your invitation…
            </div>
          ) : preview.isError ? (
            axios.isAxiosError(preview.error) && preview.error.response?.status === 410 ? (
              <div className="text-sm text-amber-700">
                This invitation has expired. Ask an admin at your workspace to send a new one.
              </div>
            ) : (
              <div className="text-sm text-rose-600">This invitation is invalid or has already been used.</div>
            )
          ) : (
            <>
              <h1 className="text-lg font-semibold">Join {preview.data?.organization_name ?? "…"}</h1>
              <p className="mb-4 text-sm text-slate-500">
                Accepting as <span className="font-medium">{preview.data?.email}</span> ({preview.data?.role}).
              </p>
              <form onSubmit={submit} className="space-y-3">
                <div>
                  <label className="label">Your name</label>
                  <input className="input" value={name} onChange={(e) => setName(e.target.value)} required />
                </div>
                <div>
                  <label className="label">Set a password</label>
                  <input className="input" type="password" minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} required />
                </div>
                {error && <div className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-600">{error}</div>}
                <button className="btn-primary w-full" disabled={busy}>{busy ? "Joining…" : "Join workspace"}</button>
              </form>
            </>
          )}
          <button className="mt-4 w-full text-center text-sm text-brand-600 hover:underline" onClick={() => navigate("/login")}>
            Back to sign in
          </button>
        </div>
      </div>
    </div>
  );
}
