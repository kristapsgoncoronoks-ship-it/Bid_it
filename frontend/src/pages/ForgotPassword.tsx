import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";

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

export default function ForgotPassword() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");

  // Always resolves the same way — the API never reveals whether the email exists.
  const request = useMutation({
    mutationFn: async () => (await api.post("/auth/forgot-password", { email })).data,
  });

  if (request.isSuccess) {
    return (
      <Shell>
        <h1 className="text-lg font-semibold">Check your inbox</h1>
        <p className="mt-2 text-sm text-slate-500">
          If an account exists for <span className="font-medium">{email}</span>, we've sent a link to reset your password. It expires in 1 hour.
        </p>
        <button className="btn-primary mt-4 w-full" onClick={() => navigate("/login")}>Back to sign in</button>
      </Shell>
    );
  }

  return (
    <Shell>
      <h1 className="text-lg font-semibold">Reset your password</h1>
      <p className="mb-4 mt-1 text-sm text-slate-500">Enter your email and we'll send you a reset link.</p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          request.mutate();
        }}
        className="space-y-3"
      >
        <div>
          <label className="label" htmlFor="email">Email</label>
          <input id="email" className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </div>
        <button className="btn-primary w-full" disabled={request.isPending}>
          {request.isPending ? "Sending…" : "Send reset link"}
        </button>
      </form>
      <button className="mt-4 w-full text-center text-sm text-brand-600 hover:underline" onClick={() => navigate("/login")}>
        Back to sign in
      </button>
    </Shell>
  );
}
