import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";

/**
 * WO-AI — the ex-client's door to their archive (owner decision 2026-08-16
 * §1.C: the archive survives a client who leaves, and "an ex-client can
 * request a one-time EXPORT of their archive; no live login is retained").
 *
 * Public on purpose: the person asking cannot sign in any more. They give the
 * address the workspace's owner used; if it is one, a one-time download link
 * goes there. The page never says whether it was — the forgot-password posture.
 */
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

export default function ArchiveExportRequest() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");

  const request = useMutation({
    mutationFn: async () => (await api.post("/archive/export/request", { email })).data,
    meta: { silent: true },
  });

  if (request.isSuccess) {
    return (
      <Shell>
        <h1 className="text-lg font-semibold">Check your inbox</h1>
        <p className="mt-2 text-sm text-slate-500">
          If <span className="font-medium">{email}</span> is the owner's address of a workspace, we're
          preparing its archive and will email a download link there. The link works once and
          expires in 7 days.
        </p>
        <button className="btn-primary mt-4 w-full" onClick={() => navigate("/login")}>Back to sign in</button>
      </Shell>
    );
  }

  return (
    <Shell>
      <h1 className="text-lg font-semibold">Request your archive</h1>
      <p className="mb-4 mt-1 text-sm text-slate-500">
        Left the workspace? Its archived invoices are kept for the retention period, and the
        owner can take a one-time export of them. Enter the owner's email address.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          request.mutate();
        }}
        className="space-y-3"
      >
        <div>
          <label className="label" htmlFor="owner-email">Owner's email</label>
          <input id="owner-email" className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </div>
        {request.isError && (
          <div role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-600">
            Couldn’t send the request — try again in a moment.
          </div>
        )}
        <button className="btn-primary w-full" disabled={request.isPending}>
          {request.isPending ? "Sending…" : "Email me a download link"}
        </button>
      </form>
      <button className="mt-4 w-full text-center text-sm text-brand-600 hover:underline" onClick={() => navigate("/login")}>
        Back to sign in
      </button>
    </Shell>
  );
}
