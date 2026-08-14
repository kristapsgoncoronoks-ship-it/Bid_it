import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Button, EmptyState, Pagination, QueryState, Skeleton } from "../components/ui";
import { api } from "../lib/api";
import { METHOD_STYLES, methodLabel, shortDate } from "../lib/format";
import type { CaptureFailureWorklist, CaptureReviewQueue } from "../lib/types";

const PAGE_SIZE = 20;

/**
 * The extraction-review queue (E1.1): documents that PARSED but are not saved to
 * an invoice yet. This is deliberately NOT the validation queue (`/review`) — that
 * screen triages invoices that already exist; this one triages what the machine
 * *read* before it becomes an invoice at all.
 */
export default function CaptureQueue() {
  const [page, setPage] = useState(1);
  const queue = useQuery<CaptureReviewQueue>({
    queryKey: ["captures", "queue", page],
    queryFn: async () =>
      (await api.get(`/invoices/captures/review?page=${page}&page_size=${PAGE_SIZE}`)).data,
  });
  // H-1: this queue only ever shows what we COULD read. Without this banner an
  // empty queue reads as "all clear" when documents may have failed capture
  // entirely — the absence has to be stated, not inferred.
  const failures = useQuery<CaptureFailureWorklist>({
    queryKey: ["captures", "failures", false],
    queryFn: async () => (await api.get("/invoices/captures/failures")).data,
  });

  return (
    <div className="space-y-6">
      {(failures.data?.unacknowledged ?? 0) > 0 && (
        <Link
          to="/captures/failures"
          className="flex items-center justify-between gap-4 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 hover:bg-amber-100"
        >
          <span>
            <strong className="font-semibold">
              {failures.data?.unacknowledged}{" "}
              {failures.data?.unacknowledged === 1 ? "document" : "documents"}
            </strong>{" "}
            arrived but could not be read, so {failures.data?.unacknowledged === 1 ? "it" : "they"}{" "}
            never became invoices.
          </span>
          <span className="shrink-0 font-medium underline">See what to do →</span>
        </Link>
      )}
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Captures</h1>
          <p className="text-sm text-slate-500">
            What we read from each uploaded document, awaiting your review. Confirming a capture is
            what creates the invoice — nothing is saved without you.
          </p>
        </div>
        <Link to="/upload" className="btn-primary shrink-0">
          Upload a document
        </Link>
      </div>

      <QueryState
        query={queue}
        loading={<Skeleton className="h-48 w-full" />}
        isEmpty={(d) => d.total === 0}
        empty={
          <div className="card p-0">
            <EmptyState
              title="Nothing waiting for review"
              description="Upload an invoice and it will appear here with everything we extracted — per field, with confidence."
              action={
                <Link to="/upload">
                  <Button variant="secondary" size="sm">
                    Upload an invoice
                  </Button>
                </Link>
              }
            />
          </div>
        }
        errorTitle="Couldn’t load the capture queue"
      >
        {(data) => (
          <>
            <div className="card overflow-x-auto p-0">
              <table className="w-full text-sm">
                <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                  <tr>
                    <th scope="col" className="px-4 py-3">Document</th>
                    <th scope="col" className="px-4 py-3">Invoice no.</th>
                    <th scope="col" className="px-4 py-3">Vendor</th>
                    <th scope="col" className="px-4 py-3">Read via</th>
                    <th scope="col" className="px-4 py-3">Needs a look</th>
                    <th scope="col" className="px-4 py-3">Received</th>
                    <th scope="col" className="px-4 py-3">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((it) => (
                    <tr
                      key={it.extraction_run_id}
                      className="border-b border-slate-100 last:border-0 hover:bg-slate-50"
                    >
                      <td className="max-w-[16rem] truncate px-4 py-3 font-medium text-slate-700">
                        {it.source_filename || "—"}
                      </td>
                      <td className="px-4 py-3">{it.invoice_number || <span className="text-slate-400">not read</span>}</td>
                      <td className="px-4 py-3 text-slate-600">
                        {it.vendor_name || <span className="text-slate-400">not read</span>}
                      </td>
                      <td className="px-4 py-3">
                        <span className={`badge ${METHOD_STYLES[it.method] ?? "bg-slate-100 text-slate-600"}`}>
                          {methodLabel(it.method)}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex flex-wrap gap-1">
                          {it.low_confidence_fields > 0 && (
                            <span className="badge bg-amber-100 text-amber-700">
                              {it.low_confidence_fields} low-confidence{" "}
                              {it.low_confidence_fields === 1 ? "field" : "fields"}
                            </span>
                          )}
                          {it.warning_count > 0 && (
                            <span className="badge bg-slate-100 text-slate-600">
                              {it.warning_count} {it.warning_count === 1 ? "warning" : "warnings"}
                            </span>
                          )}
                          {it.duplicate_candidate && (
                            <span className="badge bg-rose-100 text-rose-700">possible duplicate</span>
                          )}
                          {it.low_confidence_fields === 0 &&
                            it.warning_count === 0 &&
                            !it.duplicate_candidate && <span className="text-slate-400">clean</span>}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-slate-500">{shortDate(it.created_at)}</td>
                      <td className="px-4 py-3 text-right">
                        <Link
                          to={`/captures/${it.extraction_run_id}`}
                          className="font-medium text-brand-600 hover:underline"
                        >
                          Review →
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination page={page} total={data.total} pageSize={PAGE_SIZE} onPageChange={setPage} />
          </>
        )}
      </QueryState>
    </div>
  );
}
