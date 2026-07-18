import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { money, shortDate, VALIDATION_STYLES } from "../lib/format";
import type { InvoiceList, ValidationSettings } from "../lib/types";

function useQueue(statusVal: string) {
  return useQuery<InvoiceList>({
    queryKey: ["invoices", "validation", statusVal],
    queryFn: async () =>
      (await api.get(`/invoices?validation_status=${statusVal}&page_size=100`)).data,
  });
}

export default function Review() {
  const settings = useQuery<ValidationSettings>({
    queryKey: ["settings", "validation"],
    queryFn: async () => (await api.get("/settings/validation")).data,
  });
  const pending = useQueue("pending");
  const flagged = useQueue("flagged");

  const off = settings.data && !settings.data.ai_validation_enabled && !settings.data.human_validation_enabled;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Review queue</h1>
        <p className="text-sm text-slate-500">
          Invoices awaiting human approval, and those the automated checks flagged.
        </p>
      </div>

      {off && (
        <div className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-700">
          Validation is off. Turn on AI and/or human validation in{" "}
          <Link to="/settings" className="font-medium underline">
            Settings
          </Link>{" "}
          to populate this queue.
        </div>
      )}

      <Section title="Awaiting human review" data={pending.data} empty="Nothing waiting for approval." />
      <Section title="Flagged by AI" data={flagged.data} empty="Nothing flagged." />
    </div>
  );
}

function Section({ title, data, empty }: { title: string; data?: InvoiceList; empty: string }) {
  return (
    <div>
      <h2 className="mb-2 text-sm font-semibold text-slate-600">
        {title} {data ? `(${data.total})` : ""}
      </h2>
      {data && data.items.length === 0 ? (
        <div className="card text-sm text-slate-400">{empty}</div>
      ) : (
        <div className="card overflow-hidden p-0">
          <table className="w-full text-sm">
            <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-3">Number</th>
                <th className="px-4 py-3">Issue date</th>
                <th className="px-4 py-3 text-right">Total</th>
                <th className="px-4 py-3">Validation</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody>
              {data?.items.map((inv) => (
                <tr key={inv.id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50">
                  <td className="px-4 py-3 font-medium">{inv.invoice_number}</td>
                  <td className="px-4 py-3 text-slate-500">{shortDate(inv.issue_date)}</td>
                  <td className="px-4 py-3 text-right">{money(inv.total, inv.currency)}</td>
                  <td className="px-4 py-3">
                    <span className={`badge ${VALIDATION_STYLES[inv.validation_status] ?? ""}`}>
                      {inv.validation_status}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <Link to={`/invoices/${inv.id}`} className="text-brand-600 hover:underline">
                      Review →
                    </Link>
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
