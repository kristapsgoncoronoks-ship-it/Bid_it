import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, apiError } from "../lib/api";

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center justify-center gap-2">
          <div className="grid h-9 w-9 place-items-center rounded-lg bg-brand-500 text-sm font-bold text-white">iQ</div>
          <span className="text-xl font-semibold tracking-tight">InvoiceIQ</span>
        </div>
        <div className="card">{children}</div>
      </div>
    </div>
  );
}

export default function ResetPassword() {
  const [params] = useSearchParams();
  const token = params.get("token") || "";
  const navigate = useNavigate();
  const [password, setPassword] = useState("");

  const reset = useMutation({
    mutationFn: async () => (await api.post("/auth/reset-password", { token, new_password: password })).data,
  });

  // Invalid-token state (link opened without a token).
  if (!token) {
    return (
      <Shell>
        <h1 className="text-lg font-semibold">Reset link missing</h1>
        <p className="mt-2 text-sm text-slate-500">This link is incomplete. Request a new one from the sign-in page.</p>
        <button className="btn-primary mt-4 w-full" onClick={() => navigate("/forgot-password")}>Request a new link</button>
      </Shell>
    );
  }

  // Success state.
  if (reset.isSuccess) {
    return (
      <Shell>
        <h1 className="text-lg font-semibold">Password updated ✓</h1>
        <p className="mt-2 text-sm text-slate-500">Your password has been changed. Sign in with your new password.</p>
        <button className="btn-primary mt-4 w-full" onClick={() => navigate("/login")}>Continue to sign in</button>
      </Shell>
    );
  }

  return (
    <Shell>
      <h1 className="text-lg font-semibold">Choose a new password</h1>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          reset.mutate();
        }}
        className="mt-3 space-y-3"
      >
        <div>
          <label className="label" htmlFor="new-password">New password</label>
          <input id="new-password"
            className="input"
            type="password"
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </div>
        {/* Invalid / expired-token error surfaces here. */}
        {reset.isError && (
          <div className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-600">
            {apiError(reset.error)}. The link may have expired (valid for 1 hour) or already been used.
          </div>
        )}
        <button className="btn-primary w-full" disabled={reset.isPending}>
          {reset.isPending ? "Saving…" : "Set new password"}
        </button>
      </form>
      <button className="mt-4 w-full text-center text-sm text-brand-600 hover:underline" onClick={() => navigate("/forgot-password")}>
        Request a new link
      </button>
    </Shell>
  );
}
