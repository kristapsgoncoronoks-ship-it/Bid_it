import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Button, Card, ConfirmDialog, ErrorState, type Tone } from "../components/ui";
import { api, apiError, apiErrorCode, downloadFile } from "../lib/api";
import { money, shortDate } from "../lib/format";
import { useConfirm } from "../components/ui/useConfirm";

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
  exported_at: string | null;
  export_count: number;
  last_msg_id: string | null;
  version: number;
  created_at: string;
  report_count: number;
}

const STATUS_TONE: Record<string, Tone> = {
  open: "info",
  paid: "success",
  cancelled: "neutral",
};
const BATCH_READ_TIMEOUT_MS = 15_000;

function hasExportRecord(batch: Batch): boolean {
  return batch.export_count !== 0 || batch.exported_at !== null || batch.last_msg_id !== null;
}

type ExportKind = "csv" | "sepa";
interface PendingExport {
  batch: Batch;
  kind: ExportKind;
  confirmReexport: boolean;
  acknowledgeSkipped: boolean;
}

export default function ReimbursementsPage() {
  const { confirm, dialog } = useConfirm();
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const [refs, setRefs] = useState<Record<string, string>>({});
  const busyRef = useRef(new Set<string>());
  const refreshingRef = useRef(new Set<string>());
  const [busyBatches, setBusyBatches] = useState(new Set<string>());
  const [refreshRequired, setRefreshRequired] = useState(new Set<string>());
  // WO-9 export flows: a re-export needs explicit confirmation (the bank sees a
  // NEW message id), and employees without an IBAN must be acknowledged BY NAME.
  const [reexportAsk, setReexportAsk] = useState<PendingExport | null>(null);
  const [skippedAsk, setSkippedAsk] = useState<{ pending: PendingExport; detail: string } | null>(
    null,
  );

  const approvedQ = useQuery<{ items: Report[] }>({
    queryKey: ["expenses", "approved"],
    queryFn: async () => (await api.get("/expenses?status=approved&page_size=100", { timeout: BATCH_READ_TIMEOUT_MS })).data,
  });
  const approved = approvedQ.data;
  const batchesQ = useQuery<Batch[]>({
    queryKey: ["reimbursements"],
    queryFn: async () => (await api.get("/reimbursements")).data,
  });
  const batches = batchesQ.data;

  const invalidate = () => {
    setErr(null);
    qc.invalidateQueries({ queryKey: ["reimbursements"] });
    qc.invalidateQueries({ queryKey: ["expenses", "approved"] });
  };
  const onErr = (e: unknown) => setErr(apiError(e));

  const beginBatchAction = (id: string) => {
    if (busyRef.current.has(id)) return false;
    busyRef.current.add(id);
    setBusyBatches(new Set(busyRef.current));
    return true;
  };

  const refreshBatchStatus = async (id: string) => {
    if (refreshingRef.current.has(id)) return;
    refreshingRef.current.add(id);
    setRefreshRequired((current) => new Set([...current].filter((key) => key !== id)));
    try {
      // A fresh scoped detail read cannot reuse a list request started before
      // this command. Cancel that old list before publishing the current row.
      await qc.cancelQueries({ queryKey: ["reimbursements"] });
      const { data } = await api.get<Batch>(`/reimbursements/${id}`, { timeout: BATCH_READ_TIMEOUT_MS });
      if (
        !data || data.id !== id || !Object.prototype.hasOwnProperty.call(STATUS_TONE, data.status) ||
        !Number.isInteger(data.version) || !Number.isInteger(data.export_count) ||
        !("exported_at" in data) || !("last_msg_id" in data)
      ) throw new Error("The current batch status could not be verified.");
      qc.setQueryData<Batch[]>(["reimbursements"], (current) =>
        current?.map((batch) => batch.id === id ? data : batch),
      );
      await qc.invalidateQueries({ queryKey: ["expenses", "approved"] }, { throwOnError: true });
      busyRef.current.delete(id);
      setBusyBatches(new Set(busyRef.current));
    } catch {
      setRefreshRequired((current) => new Set(current).add(id));
    } finally {
      refreshingRef.current.delete(id);
    }
  };

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
    onSuccess: () => setErr(null),
    onError: onErr,
    onSettled: (_data, _error, batch) => refreshBatchStatus(batch.id),
  });
  const cancel = useMutation({
    mutationFn: async (id: string) => (await api.delete(`/reimbursements/${id}`)).data,
    onSuccess: () => setErr(null),
    onError: onErr,
    onSettled: (_data, _error, id) => refreshBatchStatus(id),
  });

  const doExport = async (p: PendingExport) => {
    if (!beginBatchAction(p.batch.id)) return;
    const params = new URLSearchParams();
    if (p.confirmReexport) params.set("confirm_reexport", "true");
    if (p.acknowledgeSkipped) params.set("acknowledge_skipped", "true");
    const q = params.toString() ? `?${params.toString()}` : "";
    const path = p.kind === "csv" ? "export" : "sepa";
    const ext = p.kind === "csv" ? "csv" : "xml";
    try {
      setErr(null);
      await downloadFile(
        `/reimbursements/${p.batch.id}/${path}${q}`,
        `reimbursement-${p.batch.reference || p.batch.id}.${ext}`,
        { method: "post" },
      );
    } catch (e) {
      const code = apiErrorCode(e);
      if (code === "already_exported") {
        setReexportAsk({ ...p, confirmReexport: true });
      } else if (code === "skipped_payees") {
        setSkippedAsk({ pending: { ...p, acknowledgeSkipped: true }, detail: apiError(e) });
      } else {
        setErr(apiError(e));
      }
    } finally {
      await refreshBatchStatus(p.batch.id);
    }
  };

  const items = approved?.items ?? [];
  const selectedTotal = items
    .filter((r) => picked[r.id])
    .reduce((s, r) => s + Number(r.total_eur ?? r.total), 0);

  return (
    <div className="space-y-6">
      {dialog}
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
        <div role="alert" className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-700">
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
            <tr className="text-left text-xs text-text-secondary">
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
            {approvedQ.isError && (
              <tr>
                <td colSpan={4} className="py-3">
                  <ErrorState title="Couldn’t load the approved reports" onRetry={() => approvedQ.refetch()} />
                </td>
              </tr>
            )}
            {!approvedQ.isError && items.length === 0 && (
              <tr>
                <td colSpan={4} className="py-3 text-text-secondary">
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
            <div key={b.id} className="min-w-0 rounded-lg border border-slate-200 p-3 [overflow-wrap:anywhere]">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex min-w-0 flex-wrap items-center gap-2">
                  <Badge tone={STATUS_TONE[b.status] ?? "neutral"}>{b.status}</Badge>
                  <span className="font-medium">{money(b.total_eur, "EUR")}</span>
                  <span className="text-xs text-text-secondary">
                    {b.report_count} report{b.report_count === 1 ? "" : "s"} · {b.method}
                    {b.reference ? ` · ${b.reference}` : ""}
                  </span>
                </div>
                <div className="flex min-w-0 max-w-full flex-wrap items-center gap-2">
                  {b.status === "open" && (
                    <>
                      <input
                        className="w-40 max-w-full rounded-lg border border-slate-300 px-2 py-1 text-sm"
                        placeholder="Payment reference…" aria-label="Payment reference"
                        value={refs[b.id] ?? ""}
                        onChange={(e) => setRefs({ ...refs, [b.id]: e.target.value })}
                        disabled={busyBatches.has(b.id)}
                      />
                      <Button size="sm" loading={pay.isPending && pay.variables?.id === b.id} disabled={busyBatches.has(b.id)} onClick={() => { if (beginBatchAction(b.id)) pay.mutate(b); }}>
                        Mark paid
                      </Button>
                      <Button size="sm" variant="ghost" aria-describedby={hasExportRecord(b) ? `cancel-export-${b.id}` : undefined} onClick={async () => {
                        if (hasExportRecord(b) || busyRef.current.has(b.id)) return;
                        if (await confirm({ title: "Cancel this reimbursement batch?", body: "Cancel this unexported batch and return its reports to the payable pool? The server will refuse cancellation if an export or payment has been recorded.", confirmLabel: "Cancel batch" }) && beginBatchAction(b.id)) cancel.mutate(b.id);
                      }} disabled={hasExportRecord(b) || busyBatches.has(b.id) || (cancel.isPending && cancel.variables === b.id)}>
                        Cancel
                      </Button>
                    </>
                  )}
                  {b.status !== "cancelled" && (
                    <>
                      <Button
                        size="sm"
                        variant="secondary"
                        disabled={busyBatches.has(b.id)}
                        onClick={() =>
                          doExport({
                            batch: b,
                            kind: "csv",
                            confirmReexport: false,
                            acknowledgeSkipped: false,
                          })
                        }
                      >
                        Export CSV
                      </Button>
                      <Button
                        size="sm"
                        variant="secondary"
                        disabled={busyBatches.has(b.id)}
                        title="ISO 20022 pain.001 credit-transfer file (employees with an IBAN on file)"
                        onClick={() =>
                          doExport({
                            batch: b,
                            kind: "sepa",
                            confirmReexport: false,
                            acknowledgeSkipped: false,
                          })
                        }
                      >
                        Export SEPA
                      </Button>
                    </>
                  )}
                </div>
              </div>
              {b.status === "open" && hasExportRecord(b) && (
                <p id={`cancel-export-${b.id}`} className="mt-2 max-w-prose text-sm text-text-secondary">
                  Export information is recorded for this batch, so cancellation is blocked.
                  Check the bank file’s status with your bank or accountant before arranging
                  another payment. An export does not confirm payment.
                </p>
              )}
              {refreshRequired.has(b.id) ? (
                <div className="mt-2 space-y-2">
                  <p className="text-sm text-text-secondary">
                    The current batch status could not be verified. Refresh it before another
                    batch action; this check does not repeat the previous action.
                  </p>
                  <Button size="sm" variant="secondary" onClick={() => void refreshBatchStatus(b.id)}>
                    Refresh batch status
                  </Button>
                </div>
              ) : busyBatches.has(b.id) && (
                <p role="status" className="mt-2 text-sm text-text-secondary">
                  Batch action in progress. Wait for its updated status.
                </p>
              )}
              <div className="mt-1 text-xs text-text-secondary">
                Created {shortDate(b.created_at)}
                {b.created_by ? ` by ${b.created_by}` : ""}
                {b.paid_at ? ` · paid ${shortDate(b.paid_at)}` : ""}
                {b.export_count > 0
                  ? ` · exported ${b.export_count}× (first ${shortDate(b.exported_at!)}${
                      b.last_msg_id ? `, MsgId ${b.last_msg_id}` : ""
                    })`
                  : ""}
              </div>
            </div>
          ))}
          {batchesQ.isError && (
            <ErrorState title="Couldn’t load the payout batches" onRetry={() => batchesQ.refetch()} />
          )}
          {!batchesQ.isError && (batches ?? []).length === 0 && (
            <p className="text-sm text-text-secondary">No payout batches yet.</p>
          )}
        </div>
      </Card>

      <ConfirmDialog
        open={reexportAsk !== null && !busyBatches.has(reexportAsk.batch.id)}
        onClose={() => setReexportAsk(null)}
        onConfirm={() => {
          const p = reexportAsk!;
          setReexportAsk(null);
          void doExport(p);
        }}
        title="Re-export bank file?"
        confirmLabel="Re-export with a new message id"
        tone="danger"
      >
        {reexportAsk && (
          <p className="text-sm text-slate-600">
            This batch was already exported{" "}
            {reexportAsk.batch.export_count > 0
              ? `${reexportAsk.batch.export_count}× (first ${shortDate(reexportAsk.batch.exported_at!)})`
              : ""}
            . Re-exporting produces a <strong>new file with a new message id</strong> — the bank
            will treat it as a separate payment instruction. Only continue if the previous file
            was never submitted.
          </p>
        )}
      </ConfirmDialog>

      <ConfirmDialog
        open={skippedAsk !== null && !busyBatches.has(skippedAsk.pending.batch.id)}
        onClose={() => setSkippedAsk(null)}
        onConfirm={() => {
          const p = skippedAsk!.pending;
          setSkippedAsk(null);
          void doExport(p);
        }}
        title="Some employees will not be paid"
        confirmLabel="I understand these will not be paid"
        tone="danger"
      >
        {skippedAsk && <p className="text-sm text-slate-600">{skippedAsk.detail}</p>}
      </ConfirmDialog>
    </div>
  );
}
