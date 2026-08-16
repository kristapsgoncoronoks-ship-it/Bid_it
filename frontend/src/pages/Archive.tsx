import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Button, EmptyState, QueryState, Skeleton } from "../components/ui";
import { useAuth } from "../auth/AuthContext";
import { api, apiError, downloadFile } from "../lib/api";
import { shortDate } from "../lib/format";
import { isAdminOrAbove } from "../lib/roles";
import type { ArchiveList, ArchivedInvoice } from "../lib/types";

/**
 * The platform archive, as the CLIENT sees it
 * (docs/design/platform-archive.md, the last step of the deletion chain).
 *
 * An invoice that finishes its 30 days in the recycle bin is destroyed as a live
 * row and a sealed copy lands here, where the company's own owner can read it
 * and download the source document for the retention period.
 *
 * This screen is what makes that defensible. Until it existed the archive was a
 * store clients could not see — retention done TO them, which has to be
 * explained; a store they can read is a feature they use. The API shipped first
 * and had no caller, which meant the promise recorded in the DPA was, in the
 * product, unverifiable by the person it was made to.
 *
 * Three things it deliberately does NOT do:
 *
 *  - **No restore.** There is no route to call and there should not be: the bin
 *    restores into live books, the archive only shows. Re-entering a three-year
 *    -old invoice reopens a closed period and can collide with numbers issued
 *    since. Saying so on screen is better than leaving people hunting for it.
 *  - **It states no retention window of its own.** `retention_years` and
 *    `expiry_notice_days` come off the response, so what the screen promises and
 *    what the archive enforces cannot drift apart — the same rule the Trash
 *    screen follows.
 *  - **It does not pretend the archive is permanent.** A record leaves on its
 *    expiry date, and for most clients three years is BELOW the statutory floor
 *    they are held to. The date is shown on every row and called out once it is
 *    inside the notice window, because the one unforgivable version of this
 *    feature is a record vanishing from a store the client was told keeps things.
 */
// Mirrors the route's own default (`Query(50, ge=1, le=200)`).
const PAGE_SIZE = 50;

function daysUntil(iso: string): number {
  const ms = new Date(iso).getTime() - Date.now();
  return Math.max(0, Math.ceil(ms / 86_400_000));
}

function Row({
  item,
  noticeDays,
  onError,
}: {
  item: ArchivedInvoice;
  noticeDays: number;
  onError: (m: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const left = daysUntil(item.expires_at);
  const soon = left <= noticeDays;

  const download = async () => {
    setBusy(true);
    try {
      await downloadFile(
        `/archive/${item.id}/document`,
        item.source_filename || `${item.invoice_number ?? "invoice"}.pdf`,
      );
      onError("");
    } catch (e) {
      // The row can outlive its bytes (the API says so with a 404). Surfacing
      // that plainly beats a silent no-op on the one button that carries the
      // evidentiary value of the whole feature.
      onError(apiError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <tr>
        <td className="px-4 py-3 font-medium text-slate-800">
          <button
            className="text-left hover:underline"
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
          >
            {item.invoice_number ?? "—"}
          </button>
        </td>
        <td className="px-4 py-3 text-slate-600">{item.vendor_name ?? "—"}</td>
        <td className="px-4 py-3 text-slate-600">
          {item.issue_date ? shortDate(item.issue_date) : "—"}
        </td>
        <td className="px-4 py-3 text-right tabular-nums text-slate-700">
          {item.total ? `${item.total} ${item.currency ?? ""}`.trim() : "—"}
        </td>
        <td className="px-4 py-3 text-slate-600">
          {item.original_deleted_at ? shortDate(item.original_deleted_at) : "—"}
          {item.original_deleted_by && (
            <span className="block text-xs text-slate-400">
              by {item.original_deleted_by}
            </span>
          )}
        </td>
        <td className="px-4 py-3">
          <Badge tone={soon ? "warning" : "neutral"}>
            {shortDate(item.expires_at)}
          </Badge>
          {soon && (
            <span className="block text-xs text-amber-700">
              leaves in {left} {left === 1 ? "day" : "days"}
            </span>
          )}
        </td>
        <td className="px-4 py-3 text-right">
          <Button
            size="sm"
            variant="secondary"
            disabled={!item.has_document || busy}
            title={
              item.has_document
                ? undefined
                : "This invoice was entered by hand and never had a source document."
            }
            onClick={download}
          >
            Download
          </Button>
        </td>
      </tr>
      {open && (
        <tr className="bg-slate-50/60">
          <td colSpan={7} className="px-4 py-3">
            <p className="mb-2 text-xs text-slate-500">
              Archived {shortDate(item.archived_at)} · was invoice{" "}
              <span className="font-mono">{item.original_invoice_id}</span>
            </p>
            {item.line_items.length === 0 ? (
              <p className="text-sm text-slate-500">
                No line detail was captured for this invoice.
              </p>
            ) : (
              <table className="w-full text-sm">
                <tbody className="divide-y divide-slate-200">
                  {item.line_items.map((line, i) => (
                    <tr key={i}>
                      <td className="py-1 pr-4 text-slate-700">
                        {String(line.description ?? line.product ?? "—")}
                      </td>
                      <td className="py-1 pr-4 text-right tabular-nums text-slate-600">
                        {line.quantity != null ? String(line.quantity) : ""}
                      </td>
                      <td className="py-1 text-right tabular-nums text-slate-700">
                        {line.line_total != null ? String(line.line_total) : ""}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

export default function Archive() {
  const { user } = useAuth();
  const mayRead = isAdminOrAbove(user);
  const [err, setErr] = useState<string | null>(null);
  const [page, setPage] = useState(1);

  const archive = useQuery<ArchiveList>({
    queryKey: ["archive", page],
    enabled: mayRead,
    queryFn: async () =>
      (
        await api.get(
          `/archive?limit=${PAGE_SIZE}&offset=${(page - 1) * PAGE_SIZE}`,
        )
      ).data,
  });

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Archive</h1>
          <p className="text-sm text-slate-500">
            Invoices that finished their time in Deleted invoices. They are no
            longer part of your books — this is a read-only record kept so you
            can still prove what they were.
          </p>
        </div>
        <Link to="/invoices/trash" className="btn-secondary shrink-0">
          Deleted invoices
        </Link>
      </div>

      {!mayRead ? (
        // Not a security boundary — the server refuses this router outright. It
        // is here so the person reads a sentence naming who to ask instead of a
        // bare 403.
        <div className="card p-6 text-sm text-slate-600">
          The archive holds records your company deleted, so it is readable only
          by an administrator or the company owner. Ask one of them if you need
          a copy of a deleted invoice.
        </div>
      ) : (
        <>
          {err && (
            <div className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
              {err}
            </div>
          )}

          <QueryState
            query={archive}
            loading={<Skeleton className="h-48 w-full" />}
            isEmpty={(d) => d.items.length === 0}
            empty={
              <div className="card p-0">
                <EmptyState
                  title="Nothing archived yet"
                  description="An invoice arrives here once it has spent its full time in Deleted invoices — so expect this to stay empty for the first month."
                />
              </div>
            }
            errorTitle="Couldn’t load the archive"
          >
            {(data) => (
              <>
                <p className="text-sm text-slate-500">
                  {data.total === data.items.length
                    ? `${data.total} ${data.total === 1 ? "record" : "records"} archived.`
                    : `Showing ${data.items.length} of ${data.total} archived records.`}{" "}
                  Each is kept for {data.retention_years}{" "}
                  {data.retention_years === 1 ? "year" : "years"} from the day it
                  was archived, then removed. Archived invoices cannot be
                  restored into your books — download the record or the original
                  document instead.
                </p>

                <div className="card overflow-x-auto p-0">
                  <table className="w-full text-sm">
                    <thead className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                      <tr>
                        <th className="px-4 py-3 font-medium">Invoice</th>
                        <th className="px-4 py-3 font-medium">Supplier</th>
                        <th className="px-4 py-3 font-medium">Issued</th>
                        <th className="px-4 py-3 text-right font-medium">Total</th>
                        <th className="px-4 py-3 font-medium">Deleted</th>
                        <th className="px-4 py-3 font-medium">Kept until</th>
                        <th className="px-4 py-3" />
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {data.items.map((item) => (
                        <Row
                          key={item.id}
                          item={item}
                          noticeDays={data.expiry_notice_days}
                          onError={(m) => setErr(m || null)}
                        />
                      ))}
                    </tbody>
                  </table>
                </div>

                {data.total > PAGE_SIZE && (
                  <div className="flex items-center justify-between text-sm text-slate-500">
                    <span>
                      Page {page} / {Math.max(1, Math.ceil(data.total / PAGE_SIZE))}
                    </span>
                    <div className="flex items-center gap-2">
                      <button
                        className="btn-ghost"
                        disabled={page <= 1}
                        onClick={() => setPage((p) => p - 1)}
                      >
                        Prev
                      </button>
                      <button
                        className="btn-ghost"
                        disabled={page >= Math.ceil(data.total / PAGE_SIZE)}
                        onClick={() => setPage((p) => p + 1)}
                      >
                        Next
                      </button>
                    </div>
                  </div>
                )}
              </>
            )}
          </QueryState>
        </>
      )}
    </div>
  );
}
