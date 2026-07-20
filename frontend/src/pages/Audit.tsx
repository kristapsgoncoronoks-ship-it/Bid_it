import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../lib/api";
import type { AuditEvent, ChainStatus, Paginated } from "../lib/types";

const PAGE_SIZE = 50;

// Friendly labels for the dot-namespaced action codes the backend records.
const ACTION_LABELS: Record<string, string> = {
  "auth.login": "Signed in",
  "auth.register": "Workspace created",
  "invoice.create": "Invoice created",
  "invoice.delete": "Invoice deleted",
  "invoice.validate": "Invoice validated",
  "document.download": "Document downloaded",
  "inbound.confirm": "Inbound invoice confirmed",
  "module.toggle": "Module toggled",
  "user.role_change": "Role changed",
  "user.deactivate": "User deactivated",
  "user.invite": "User invited",
};

const ACTION_STYLES: Record<string, string> = {
  "invoice.delete": "bg-rose-100 text-rose-700",
  "user.deactivate": "bg-rose-100 text-rose-700",
  "user.role_change": "bg-amber-100 text-amber-700",
  "module.toggle": "bg-amber-100 text-amber-700",
  "document.download": "bg-sky-100 text-sky-700",
};

function actionLabel(a: string) {
  return ACTION_LABELS[a] ?? a;
}

function stamp(iso: string) {
  const d = new Date(iso);
  return d.toLocaleString("en-GB", {
    day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

function metaSummary(meta: Record<string, unknown> | null) {
  if (!meta) return "";
  return Object.entries(meta).map(([k, v]) => `${k}: ${v}`).join(" · ");
}

export default function Audit() {
  const [action, setAction] = useState("");
  const [page, setPage] = useState(1);

  const events = useQuery<Paginated<AuditEvent>>({
    queryKey: ["audit", "events", action, page],
    queryFn: async () =>
      (await api.get("/audit", { params: { action: action || undefined, page, page_size: PAGE_SIZE } })).data,
  });

  const verify = useMutation<ChainStatus>({
    mutationFn: async () => (await api.get("/audit/verify")).data,
  });

  const total = events.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const actionsSeen = Array.from(new Set(events.data?.items.map((e) => e.action) ?? [])).sort();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Audit log</h1>
        <p className="text-sm text-slate-500">
          A tamper-evident, append-only record of who did what in this workspace. Each event is
          hash-chained to the one before it, so a deleted or edited row breaks the chain.
        </p>
      </div>

      {/* Integrity check */}
      <div className="card flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-sm font-medium text-slate-600">Chain integrity</div>
          <div className="text-xs text-slate-400">
            Re-hashes every event and reports the first break, if any.
          </div>
        </div>
        <div className="flex items-center gap-3">
          {verify.data && (
            <span
              className={`badge ${verify.data.ok ? "bg-emerald-100 text-emerald-700" : "bg-rose-100 text-rose-700"}`}
            >
              {verify.data.ok
                ? `Intact · ${verify.data.events} events`
                : `Broken at #${verify.data.broken_at_seq}`}
            </span>
          )}
          {verify.data && !verify.data.ok && verify.data.detail && (
            <span className="text-xs text-rose-600">{verify.data.detail}</span>
          )}
          <button className="btn-primary py-1 text-xs" disabled={verify.isPending} onClick={() => verify.mutate()}>
            {verify.isPending ? "Verifying…" : "Verify integrity"}
          </button>
        </div>
      </div>

      {/* Filter */}
      <div className="flex flex-wrap items-center gap-3">
        <label className="text-sm text-slate-500">Filter by action</label>
        <select
          className="input w-64 py-1"
          value={action}
          onChange={(e) => { setAction(e.target.value); setPage(1); }}
        >
          <option value="">All actions</option>
          {actionsSeen.map((a) => (
            <option key={a} value={a}>{actionLabel(a)}</option>
          ))}
        </select>
        <span className="text-xs text-slate-400">{total} event{total === 1 ? "" : "s"}</span>
      </div>

      {/* Events */}
      <div className="card overflow-x-auto p-0">
        <table className="w-full text-sm">
          <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-3 text-right">#</th>
              <th className="px-4 py-3">When</th>
              <th className="px-4 py-3">Actor</th>
              <th className="px-4 py-3">Action</th>
              <th className="px-4 py-3">Target</th>
              <th className="px-4 py-3">Details</th>
            </tr>
          </thead>
          <tbody>
            {events.data?.items.map((e) => (
              <tr key={e.id} className="border-b border-slate-100 last:border-0">
                <td className="px-4 py-3 text-right font-mono text-xs text-slate-400 tabular-nums">{e.seq}</td>
                <td className="px-4 py-3 whitespace-nowrap text-slate-500 tabular-nums">{stamp(e.at)}</td>
                <td className="px-4 py-3 text-slate-600">{e.actor_email ?? <span className="text-slate-400">system</span>}</td>
                <td className="px-4 py-3">
                  <span className={`badge ${ACTION_STYLES[e.action] ?? "bg-slate-100 text-slate-600"}`}>
                    {actionLabel(e.action)}
                  </span>
                </td>
                <td className="px-4 py-3 text-slate-500">
                  {e.target_type ? <span className="text-xs">{e.target_type}</span> : "—"}
                </td>
                <td className="px-4 py-3 text-xs text-slate-400">{metaSummary(e.meta) || "—"}</td>
              </tr>
            ))}
            {events.data && events.data.items.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-sm text-slate-400">
                  No events yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {pages > 1 && (
        <div className="flex items-center justify-between text-sm">
          <button
            className="btn-ghost py-1 text-xs"
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            Previous
          </button>
          <span className="text-xs text-slate-400">Page {page} of {pages}</span>
          <button
            className="btn-ghost py-1 text-xs"
            disabled={page >= pages}
            onClick={() => setPage((p) => Math.min(pages, p + 1))}
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
