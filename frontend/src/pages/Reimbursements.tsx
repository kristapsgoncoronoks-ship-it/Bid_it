import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Button, Card, type Tone } from "../components/ui";
import { api, apiError, downloadFile } from "../lib/api";
import { money, shortDate } from "../lib/format";

interface Report {
  id: string;
  title: string;
  employee_name: string;
  total: string;
  currency: string;
  total_eur: string | null;
  status: string;
}
interface Batch {
  id: string;
  reference: string | null;
  method: string;
  status: string;
  total_eur: string;
  paid_at: string | null;
  created_by: string | null;
  version: number;
  created_at: string;
  report_count: number;
}

const STATUS_TONE: Record<string, Tone> = {
  open: "info",
  paid: "success",
  cancelled: "neutral",
};

export default function ReimbursementsPage() {
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const [refs, setRefs] = useState<Record<string, string>>({});

  const { data: approved } = useQuery<{ items: Report[] }>({
    queryKey: ["expenses", "approved"],
    queryFn: async () => (await api.get("/expenses?status=approved&page_size=100")).data,
  });
  const { data: batches } = useQuery<Batch[]>({
    queryKey: ["reimbursements"],
    queryFn: async () => (await api.get("/reimbursements")).data,
  });

  const invalidate = () => {
    setErr(null);
    qc.invalidateQueries({ queryKey: ["reimbursements"] });
    qc.invalidateQueries({ queryKey: ["expenses", "approved"] });
  };
  const onErr = (e: unknown) => setErr(apiError(e));

  const create = useMutation({
    mutationFn: async () =>
      (
        await api.post("/reimbursements", {
          report_ids: Object.keys(picked).filter((k) => picked[k]),
          method: "bank_transfer",
        })
      ).data,
    onSuccess: () => {
      setPicked({});
      invalidate();
    },
    onError: onErr,
  });
  const pay = useMutation({
    mutationFn: async (b: Batch) =>
      (await api.post(`/reimbursements/${b.id}/pay`, { version: b.version, reference: refs[b.id] || null }))
        .data,
    onSuccess: invalidate,
    onError: onErr,
  });
  const cancel = useMutation({
    mutationFn: async (id: string) => (await api.delete(`/reimbursements/${id}`)).data,
    onSuccess: invalidate,
    onError: onErr,
  });

  const items = approved?.items ?? [];
  const selectedTotal = items
    .filter((r) => picked[r.id])
    .reduce((s, r) => s + Number(r.total_eur ?? r.total), 0);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Reimbursements</h1>
          <p className="text-slate-500">Pay approved expense reports in a batch.</p>
        </div>
        <Link to="/expenses" className="text-sm text-brand-600 hover:underline">
          ← Expenses
        </Link>
      </div>

      {err && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-700">
          {err}
        </div>
      )}

      {/* Approved reports → new batch */}
      <Card>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-700">Approved — awaiting payout</h2>
          <Button
            size="sm"
            disabled={selectedTotal === 0}
            loading={create.isPending}
            onClick={() => create.mutate()}
          >
            Create payout batch ({money(selectedTotal, "EUR")})
          </Button>
        </div>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-slate-400">
              <th className="py-1"></th>
              <th className="py-1">Report</th>
              <th className="py-1">Employee</th>
              <th className="py-1 text-right">Amount</th>
            </tr>
          </thead>
          <tbody>
            {items.map((r) => (
              <tr key={r.id} className="border-t border-slate-100">
                <td className="py-1">
                  <input
                    type="checkbox"
                    checked={!!picked[r.id]}
                    onChange={(e) => setPicked({ ...picked, [r.id]: e.target.checked })}
                    aria-label={`Select ${r.title}`}
                  />
                </td>
                <td className="py-1">{r.title}</td>
                <td className="py-1 text-slate-500">{r.employee_name}</td>
                <td className="py-1 text-right tabular-nums">{money(r.total, r.currency)}</td>
              </tr>
            ))}
            {items.length === 0 && (
              <tr>
                <td colSpan={4} className="py-3 text-slate-400">
                  No approved reports awaiting payout.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Card>

      {/* Existing batches */}
      <Card>
        <h2 className="mb-3 text-sm font-semibold text-slate-700">Payout batches</h2>
        <div className="space-y-3">
          {(batches ?? []).map((b) => (
            <div key={b.id} className="rounded-lg border border-slate-200 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <Badge tone={STATUS_TONE[b.status] ?? "neutral"}>{b.status}</Badge>
                  <span className="font-medium">{money(b.total_eur, "EUR")}</span>
                  <span className="text-xs text-slate-400">
                    {b.report_count} report{b.report_count === 1 ? "" : "s"} · {b.method}
                    {b.reference ? ` · ${b.reference}` : ""}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  {b.status === "open" && (
                    <>
                      <input
                        className="w-40 rounded-lg border border-slate-300 px-2 py-1 text-sm"
                        placeholder="Payment reference…"
                        value={refs[b.id] ?? ""}
                        onChange={(e) => setRefs({ ...refs, [b.id]: e.target.value })}
                      />
                      <Button size="sm" loading={pay.isPending} onClick={() => pay.mutate(b)}>
                        Mark paid
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => cancel.mutate(b.id)}>
                        Cancel
                      </Button>
                    </>
                  )}
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() =>
                      downloadFile(
                        `/reimbursements/${b.id}/export`,
                        `reimbursement-${b.reference || b.id}.csv`,
                      )
                    }
                  >
                    Export CSV
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    title="ISO 20022 pain.001 credit-transfer file (employees with an IBAN on file)"
                    onClick={() =>
                      downloadFile(
                        `/reimbursements/${b.id}/sepa`,
                        `reimbursement-${b.reference || b.id}.xml`,
                      )
                    }
                  >
                    Export SEPA
                  </Button>
                </div>
              </div>
              <div className="mt-1 text-xs text-slate-400">
                Created {shortDate(b.created_at)}
                {b.created_by ? ` by ${b.created_by}` : ""}
                {b.paid_at ? ` · paid ${shortDate(b.paid_at)}` : ""}
              </div>
            </div>
          ))}
          {(batches ?? []).length === 0 && (
            <p className="text-sm text-slate-400">No payout batches yet.</p>
          )}
        </div>
      </Card>
    </div>
  );
}
