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
export default function Trash() {
  const qc = useQueryClient();
  const { user } = useAuth();
  const mayRestore = isAdminOrAbove(user);
  const [err, setErr] = useState<string | null>(null);

  const bin = useQuery<BinList>({
    queryKey: ["invoices", "trash"],
    queryFn: async () => (await api.get("/invoices/trash")).data,
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
        <Link to="/invoices" className="btn-secondary shrink-0">
          Back to invoices
        </Link>
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
              {data.total} {data.total === 1 ? "invoice" : "invoices"} deleted.
              Each one can be restored for {data.retention_days} days.
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
          </>
        )}
      </QueryState>
    </div>
  );
}
