import { useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { apiError } from "../lib/api";

export default function Login() {
  const { user, login, register } = useAuth();
  const navigate = useNavigate();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [orgName, setOrgName] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to="/" replace />;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "login") await login(email, password);
      else await register(orgName, name, email, password);
      navigate("/");
    } catch (err) {
      setError(apiError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center justify-center gap-2">
          <div className="grid h-9 w-9 place-items-center rounded-lg bg-brand-500 text-sm font-bold text-white">
            iQ
          </div>
          <span className="text-xl font-semibold tracking-tight">InvoiceIQ</span>
        </div>
        <div className="card">
          <h1 className="mb-1 text-lg font-semibold">
            {mode === "login" ? "Sign in" : "Create your workspace"}
          </h1>
          <p className="mb-4 text-sm text-slate-500">
            Turn invoices into spend analytics.
          </p>
          <form onSubmit={submit} className="space-y-3">
            {mode === "register" && (
              <>
                <div>
                  <label className="label">Organization</label>
                  <input className="input" value={orgName} onChange={(e) => setOrgName(e.target.value)} required />
                </div>
                <div>
                  <label className="label">Your name</label>
                  <input className="input" value={name} onChange={(e) => setName(e.target.value)} required />
                </div>
              </>
            )}
            <div>
              <label className="label">Email</label>
              <input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
            </div>
            <div>
              <label className="label">Password</label>
              <input
                className="input"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                minLength={8}
                required
              />
            </div>
            {error && <div className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-600">{error}</div>}
            <button className="btn-primary w-full" disabled={busy}>
              {busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create workspace"}
            </button>
          </form>
          <button
            className="mt-4 w-full text-center text-sm text-brand-600 hover:underline"
            onClick={() => {
              setMode(mode === "login" ? "register" : "login");
              setError(null);
            }}
          >
            {mode === "login" ? "Need an account? Register" : "Have an account? Sign in"}
          </button>
        </div>
        {mode === "login" && (
          <p className="mt-4 text-center text-xs text-slate-400">
            Demo: demo@invoiceiq.app / demo1234
          </p>
        )}
      </div>
    </div>
  );
}
