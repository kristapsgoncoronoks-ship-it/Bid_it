import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api } from "../lib/api";

/** The client portal (WO-I) — what a CUSTOMER sees when they open their
 * magic link. Public page: the token in the URL is the credential; no app
 * account, no password. Shows exactly their world: offers to decide,
 * invoices with status, documents someone explicitly shared. */

interface PortalOffer {
  offer_id: string;
  number: string;
  version: number;
  title: string | null;
  status: string;
  total: string;
  currency: string;
  project: string;
  lines: { description?: string; amount?: string }[];
  decidable: boolean;
}

interface PortalOut {
  organization: string;
  customer: string;
  offers: PortalOffer[];
  invoices: { number: string; total: string; currency: string; status: string; issued_at: string | null }[];
  documents: { document_id: string; filename: string; kind: string; project: string }[];
}

const STATUS_LABEL: Record<string, string> = {
  sent: "awaiting your decision",
  accepted: "accepted",
  rejected: "declined",
  draft: "being prepared",
};

export default function Portal() {
  const { token } = useParams<{ token: string }>();
  const qc = useQueryClient();

  const summary = useQuery<PortalOut>({
    queryKey: ["portal", token],
    queryFn: async () => (await api.get(`/portal/${token}`)).data,
    retry: false,
  });

  const decide = useMutation({
    mutationFn: async (arg: { offer_id: string; decision: "accepted" | "rejected" }) =>
      (await api.post(`/portal/${token}/offers/${arg.offer_id}/decision`, {
        decision: arg.decision,
      })).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["portal", token] }),
  });

  if (summary.isLoading)
    return <div className="mx-auto max-w-3xl p-8 text-sm text-slate-400">Loading…</div>;
  if (summary.isError || !summary.data)
    return (
      <div className="mx-auto max-w-3xl p-8">
        <div className="card p-6 text-sm text-slate-600">
          This link is no longer valid. Please ask your contact for a fresh one.
        </div>
      </div>
    );

  const d = summary.data;

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="mx-auto max-w-3xl space-y-6 p-4 sm:p-8">
        <div>
          <p className="text-xs uppercase tracking-wide text-slate-400">{d.organization}</p>
          <h1 className="text-2xl font-semibold tracking-tight">Hello, {d.customer}</h1>
          <p className="text-sm text-slate-500">
            Your offers, invoices and documents — always the current state.
          </p>
        </div>

        <section className="space-y-3">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">Offers</h2>
          {d.offers.length === 0 ? (
            <div className="card p-4 text-sm text-slate-400">No offers yet.</div>
          ) : (
            d.offers.map((o) => (
              <div key={o.offer_id} className="card space-y-3 p-5">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <div>
                    <p className="font-medium text-slate-800">
                      {o.number} {o.title ? `— ${o.title}` : ""}
                    </p>
                    <p className="text-xs text-slate-400">
                      {o.project} · {STATUS_LABEL[o.status] ?? o.status}
                    </p>
                  </div>
                  <p className="text-lg font-semibold tabular-nums">
                    {o.total} {o.currency}
                  </p>
                </div>
                {o.lines.length > 0 && (
                  <ul className="divide-y divide-slate-100 text-sm">
                    {o.lines.map((l, i) => (
                      <li key={i} className="flex justify-between gap-3 py-1.5">
                        <span className="text-slate-600">{l.description || `Item ${i + 1}`}</span>
                        {l.amount && <span className="tabular-nums text-slate-700">{l.amount}</span>}
                      </li>
                    ))}
                  </ul>
                )}
                {o.decidable && (
                  <div className="flex gap-2">
                    <button
                      className="btn-primary"
                      disabled={decide.isPending}
                      onClick={() => decide.mutate({ offer_id: o.offer_id, decision: "accepted" })}
                    >
                      Accept offer
                    </button>
                    <button
                      className="btn-secondary"
                      disabled={decide.isPending}
                      onClick={() => {
                        if (window.confirm("Decline this offer?"))
                          decide.mutate({ offer_id: o.offer_id, decision: "rejected" });
                      }}
                    >
                      Decline
                    </button>
                  </div>
                )}
              </div>
            ))
          )}
        </section>

        <section className="space-y-3">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">Invoices</h2>
          {d.invoices.length === 0 ? (
            <div className="card p-4 text-sm text-slate-400">No invoices yet.</div>
          ) : (
            <div className="card overflow-x-auto p-0">
              <table className="w-full text-sm">
                <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase text-slate-500">
                  <tr>
                    <th className="px-4 py-2">Invoice</th>
                    <th className="px-4 py-2">Issued</th>
                    <th className="px-4 py-2 text-right">Total</th>
                    <th className="px-4 py-2">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {d.invoices.map((inv, i) => (
                    <tr key={i}>
                      <td className="px-4 py-2 font-medium text-slate-700">{inv.number}</td>
                      <td className="px-4 py-2 text-slate-500">
                        {inv.issued_at ? inv.issued_at.slice(0, 10) : "—"}
                      </td>
                      <td className="px-4 py-2 text-right tabular-nums">
                        {inv.total} {inv.currency}
                      </td>
                      <td className="px-4 py-2 text-slate-500">{inv.status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="space-y-3">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            Documents
          </h2>
          {d.documents.length === 0 ? (
            <div className="card p-4 text-sm text-slate-400">Nothing shared yet.</div>
          ) : (
            <div className="card divide-y divide-slate-100 p-0">
              {d.documents.map((doc) => (
                <a
                  key={doc.document_id}
                  className="flex items-center justify-between px-4 py-3 text-sm hover:bg-slate-50"
                  href={`${api.defaults.baseURL}/portal/${token}/documents/${doc.document_id}`}
                >
                  <span className="text-slate-700">{doc.filename}</span>
                  <span className="text-xs text-slate-400">
                    {doc.kind} · {doc.project}
                  </span>
                </a>
              ))}
            </div>
          )}
        </section>

        <p className="pb-6 text-center text-xs text-slate-400">
          This private page was shared with you by {d.organization}. If anything
          looks wrong, reply to your contact — do not forward this link.
        </p>
      </div>
    </div>
  );
}
