import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import {
  Badge,
  Button,
  EmptyState,
  QueryState,
  Skeleton,
} from "../components/ui";
import { useAuth } from "../auth/AuthContext";
import { api, apiError } from "../lib/api";
import { shortDate } from "../lib/format";
import { isAdminOrAbove } from "../lib/roles";
import type { BinList } from "../lib/types";

/**
 * The recycle bin (docs/design/deletion-and-archive.md, step 2).
 *
 * Deleting an invoice no longer destroys it — it comes here, and an admin or the
 * company owner can put it back. The screen exists because a bin nobody can look
 * inside is indistinguishable from no bin at all: the operator's belief that a
 * mistake is recoverable is worth nothing until they can see the record.
 *
 * Two things this page deliberately does NOT do:
 *
 *  - it never states the retention window itself. `retention_days` comes off the
 *    response, so what the screen promises and what the purge enforces cannot
 *    drift apart;
 *  - it does not hide Restore from those who cannot use it. A finance manager
 *    can delete but not restore, and a button that is visibly disabled with the
 *    reason attached tells them who to ask; a button that is simply absent
 *    leaves them thinking the record is unrecoverable.
 */
// Mirrors the route's own default (`Query(50, ge=1, le=200)`).
const PAGE_SIZE = 50;

export default function Trash() {
  const qc = useQueryClient();
  const { user } = useAuth();
  const mayRestore = isAdminOrAbove(user);
  const [err, setErr] = useState<string | null>(null);
  const [page, setPage] = useState(1);

  const bin = useQuery<BinList>({
    queryKey: ["invoices", "trash", page],
    // The server pages this and ALWAYS did; the first version of this screen
    // simply never asked, took the default 50, and then printed the unpaginated
    // `total` above them. After one bulk delete that read "200 invoices deleted"
    // over 50 rows with no way to reach the rest — the screen stating something
    // untrue about deleted records, which is the exact failure the whole feature
    // exists to prevent.
    queryFn: async () =>
      (
        await api.get(
          `/invoices/trash?limit=${PAGE_SIZE}&offset=${(page - 1) * PAGE_SIZE}`,
        )
      ).data,
  });

  const restore = useMutation({
    mutationFn: async (invoiceId: string) =>
      (await api.post(`/invoices/${invoiceId}/restore`)).data,
    onSuccess: () => {
      setErr(null);
      // The invoice list changes too — it just gained a row back.
      qc.invalidateQueries({ queryKey: ["invoices"] });
    },
    onError: (e: unknown) => setErr(apiError(e)),
  });

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Deleted invoices
          </h1>
          <p className="text-sm text-slate-500">
            Removed from your workspace but not yet gone. Restoring one puts it
            back into your books exactly as it was.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {/* The next step of the chain, and the only place a client can reach
              it. Named without a retention figure: this response carries the
              BIN's window, not the archive's, and a screen must not quote a
              promise it has no source for. */}
          <Link to="/invoices/archive" className="btn-ghost">
            Archive
          </Link>
          <Link to="/invoices" className="btn-secondary">
            Back to invoices
          </Link>
        </div>
      </div>

      {err && (
        <div className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {err}
        </div>
      )}

      <QueryState
        query={bin}
        loading={<Skeleton className="h-48 w-full" />}
        isEmpty={(d) => d.items.length === 0}
        empty={
          <div className="card p-0">
            <EmptyState
              title="Nothing deleted"
              description="Invoices you delete appear here first, so a mistake can be undone."
            />
          </div>
        }
        errorTitle="Couldn’t load deleted invoices"
      >
        {(data) => (
          <>
            <p className="text-sm text-slate-500">
              {data.total === data.items.length
                ? `${data.total} ${data.total === 1 ? "invoice" : "invoices"} deleted.`
                : `Showing ${data.items.length} of ${data.total} deleted invoices.`}{" "}
              Each one can be restored for {data.retention_days} days. After
              that it leaves your books and a read-only copy is kept in the{" "}
              <Link to="/invoices/archive" className="text-brand-600 hover:underline">
                archive
              </Link>
              .
            </p>

            <div className="card overflow-x-auto p-0">
              <table className="w-full text-sm">
                <thead className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="px-4 py-3 font-medium">Invoice</th>
                    <th className="px-4 py-3 font-medium">Supplier</th>
                    <th className="px-4 py-3 text-right font-medium">Total</th>
                    <th className="px-4 py-3 font-medium">Deleted</th>
                    <th className="px-4 py-3 font-medium">Time left</th>
                    <th className="px-4 py-3" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {data.items.map((item) => (
                    <tr key={item.invoice_id}>
                      <td className="px-4 py-3 font-medium text-slate-800">
                        {item.invoice_number ?? "—"}
                      </td>
                      <td className="px-4 py-3 text-slate-600">
                        {item.vendor_name ?? "—"}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-slate-700">
                        {item.total
                          ? `${item.total} ${item.currency ?? ""}`.trim()
                          : "—"}
                      </td>
                      <td className="px-4 py-3 text-slate-600">
                        {shortDate(item.deleted_at)}
                        {item.deleted_by && (
                          <span className="block text-xs text-slate-400">
                            by {item.deleted_by}
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        {/* 0 does not mean gone — it means the purge may take it
                            at any point, which is the moment to act, so it is
                            the one value called out. */}
                        <Badge tone={item.days_left <= 3 ? "warning" : "neutral"}>
                          {item.days_left === 0
                            ? "Due to be removed"
                            : `${item.days_left} ${item.days_left === 1 ? "day" : "days"}`}
                        </Badge>
                      </td>
                      <td className="px-4 py-3 text-right">
                        <Button
                          size="sm"
                          variant="secondary"
                          disabled={!mayRestore || restore.isPending}
                          title={
                            mayRestore
                              ? undefined
                              : "Only an administrator or the company owner can restore an invoice."
                          }
                          onClick={() => restore.mutate(item.invoice_id)}
                        >
                          Restore
                        </Button>
                      </td>
                    </tr>
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

      <OtherBinned mayRestore={mayRestore} />
    </div>
  );
}

interface OtherBinnedItem {
  kind: string;
  label: string;
  id: string;
  summary: Record<string, string | null>;
  deleted_at: string;
  deleted_by: string | null;
  days_left: number;
}

/** WO-M: the generic bin — expense reports, inbox transactions, recurring
 * schedules and invoice attachments live under the same 30-day promise. */
function OtherBinned({ mayRestore }: { mayRestore: boolean }) {
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const other = useQuery<{ items: OtherBinnedItem[]; retention_days: number }>({
    queryKey: ["invoices", "trash", "other"],
    queryFn: async () => (await api.get("/invoices/trash/other")).data,
  });

  const restore = useMutation({
    mutationFn: async (i: OtherBinnedItem) =>
      (await api.post(`/invoices/trash/other/${i.kind}/${i.id}/restore`)).data,
    onSuccess: () => {
      setErr(null);
      qc.invalidateQueries({ queryKey: ["invoices", "trash", "other"] });
    },
    onError: (e) => setErr(apiError(e)),
  });

  const items = Array.isArray(other.data?.items) ? other.data.items : [];
  if (items.length === 0 && !err) return null;

  return (
    <div className="card p-6">
      <h2 className="mb-1 text-sm font-semibold uppercase tracking-wide text-slate-500">
        Other deleted items
      </h2>
      <p className="mb-3 text-xs text-slate-400">
        Expense reports, inbox transactions, recurring schedules and invoice
        attachments — same {other.data?.retention_days ?? 30}-day window, then gone.
      </p>
      {err && (
        <div className="mb-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
          {err}
        </div>
      )}
      <table className="w-full text-sm">
        <thead className="text-left text-xs uppercase tracking-wide text-slate-400">
          <tr>
            <th className="py-1">What</th>
            <th className="py-1">Details</th>
            <th className="py-1">Deleted by</th>
            <th className="py-1 text-right">Days left</th>
            <th className="py-1"></th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {items.map((i) => (
            <tr key={`${i.kind}:${i.id}`}>
              <td className="py-1.5 text-slate-700">{i.label}</td>
              <td className="py-1.5 text-slate-500">
                {Object.values(i.summary).filter(Boolean).join(" · ")}
              </td>
              <td className="py-1.5 text-xs text-slate-400">{i.deleted_by ?? "—"}</td>
              <td className="py-1.5 text-right tabular-nums">{i.days_left}</td>
              <td className="py-1.5 text-right">
                <button
                  className="btn-ghost text-sm"
                  disabled={!mayRestore || restore.isPending}
                  title={mayRestore ? undefined : "Only an admin or the owner can restore"}
                  onClick={() => restore.mutate(i)}
                >
                  Restore
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
