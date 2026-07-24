import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
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

export default function VerifyEmail() {
  const [params] = useSearchParams();
  const token = params.get("token") || "";
  const navigate = useNavigate();

  const verify = useMutation({
    mutationFn: async () => (await api.post("/auth/verify-email", { token })).data,
  });

  // Fire once on mount when a token is present.
  const fired = useRef(false);
  useEffect(() => {
    if (token && !fired.current) {
      fired.current = true;
      verify.mutate();
    }
  }, [token, verify]);

  // Invalid-token state (no token at all).
  if (!token) {
    return (
      <Shell>
        <h1 className="text-lg font-semibold">Verification link missing</h1>
        <p className="mt-2 text-sm text-slate-500">
          This link is incomplete. Open the most recent confirmation email and click the button again.
        </p>
        <button className="btn-primary mt-4 w-full" onClick={() => navigate("/login")}>Back to sign in</button>
      </Shell>
    );
  }

  // Loading state.
  if (verify.isPending || verify.isIdle) {
    return (
      <Shell>
        <div className="flex items-center gap-3 text-sm text-slate-500">
          <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-brand-500" />
          Confirming your email…
        </div>
      </Shell>
    );
  }

  // Invalid / expired-token state.
  if (verify.isError) {
    return (
      <Shell>
        <h1 className="text-lg font-semibold">Link invalid or expired</h1>
        <p className="mt-2 text-sm text-slate-500">
          {apiError(verify.error)}. Verification links expire after 24 hours — sign in and request a new one from your account.
        </p>
        <button className="btn-primary mt-4 w-full" onClick={() => navigate("/login")}>Back to sign in</button>
      </Shell>
    );
  }

  // Success state.
  return (
    <Shell>
      <h1 className="text-lg font-semibold">Email confirmed ✓</h1>
      <p className="mt-2 text-sm text-slate-500">Your email address is verified. You can sign in now.</p>
      <button className="btn-primary mt-4 w-full" onClick={() => navigate("/login")}>Continue to sign in</button>
    </Shell>
  );
}
