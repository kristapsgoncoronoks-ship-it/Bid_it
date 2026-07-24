import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, downloadFile } from "../lib/api";
import { Badge, Button, Card, DataTable, EmptyState, type Column, type Tone } from "../components/ui";
import type { AuditEvent, ChainStatus, Paginated } from "../lib/types";

const PAGE_SIZE = 50;

// Friendly labels for the dot-namespaced action codes the backend records.
const ACTION_LABELS: Record<string, string> = {
  "auth.login": "Signed in",
  "auth.register": "Workspace created",
  "invoice.create": "Invoice created",
  "invoice.delete": "Invoice deleted",
  "invoice.validate": "Invoice validated",
  "issued.payment": "Payment recorded",
  "issued.sent": "Invoice sent",
  "issued.reminder": "Reminder sent",
  "issued.penalty_invoice": "Penalty invoice",
  "document.download": "Document downloaded",
  "inbound.confirm": "Inbound invoice confirmed",
  "module.toggle": "Module toggled",
  "user.role_change": "Role changed",
  "user.approver_change": "Approver changed",
  "user.deactivate": "User deactivated",
  "user.invite": "User invited",
  "partner.create": "Partner created",
  "partner.document_sign": "Document signed",
};

// Map an action to a semantic badge tone.
const ACTION_TONE: Record<string, Tone> = {
  "invoice.delete": "danger",
  "user.deactivate": "danger",
  "user.role_change": "warning",
  "user.approver_change": "warning",
  "module.toggle": "warning",
  "document.download": "info",
  "partner.document_sign": "success",
  "issued.penalty_invoice": "danger",
};

const label = (a: string) => ACTION_LABELS[a] ?? a;

function stamp(iso: string) {
  return new Date(iso).toLocaleString("en-GB", {
    day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

function metaSummary(meta: Record<string, unknown> | null) {
  if (!meta) return "—";
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
  const verify = useMutation<ChainStatus>({ mutationFn: async () => (await api.get("/audit/verify")).data });

  const total = events.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const actionsSeen = Array.from(new Set(events.data?.items.map((e) => e.action) ?? [])).sort();

  const columns: Column<AuditEvent>[] = [
    { key: "seq", header: "#", align: "right", width: "3.5rem",
      cell: (e) => <span className="font-mono text-xs text-slate-400 tabular-nums">{e.seq}</span> },
    { key: "when", header: "When",
      cell: (e) => <span className="whitespace-nowrap text-slate-500 tabular-nums">{stamp(e.at)}</span> },
    { key: "actor", header: "Actor",
      cell: (e) => e.actor_email ?? <span className="text-slate-400">system</span> },
    { key: "action", header: "Action",
      cell: (e) => <Badge tone={ACTION_TONE[e.action] ?? "neutral"}>{label(e.action)}</Badge> },
    { key: "target", header: "Target",
      cell: (e) => e.target_type ? <span className="text-xs text-slate-500">{e.target_type}</span> : "—" },
    { key: "details", header: "Details",
      cell: (e) => <span className="text-xs text-slate-400">{metaSummary(e.meta)}</span> },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Audit log</h1>
        <p className="text-sm text-slate-500">
          A tamper-evident, append-only record of who did what in this workspace. Each event is
          hash-chained to the one before it, so a deleted or edited row breaks the chain.
        </p>
      </div>

      <Card
        title="Chain integrity"
        actions={
          <div className="flex items-center gap-3">
            {verify.data && (
              <Badge tone={verify.data.ok ? "success" : "danger"}>
                {verify.data.ok ? `Intact · ${verify.data.events} events` : `Broken at #${verify.data.broken_at_seq}`}
              </Badge>
            )}
            {verify.data && !verify.data.ok && verify.data.detail && (
              <span className="text-xs text-rose-600">{verify.data.detail}</span>
            )}
            <Button variant="secondary" size="sm" onClick={() => downloadFile("/audit/export?fmt=csv", "audit-log.csv")}>
              Export CSV
            </Button>
            <Button variant="secondary" size="sm" onClick={() => downloadFile("/audit/export?fmt=json", "audit-log.json")}>
              Export JSON
            </Button>
            <Button variant="primary" size="sm" loading={verify.isPending} onClick={() => verify.mutate()}>
              Verify integrity
            </Button>
          </div>
        }
      >
        <p className="text-xs text-slate-400">Re-hashes every event and reports the first break, if any.</p>
      </Card>

      <div className="flex flex-wrap items-center gap-3">
        <label htmlFor="audit-filter" className="text-sm text-slate-500">Filter by action</label>
        <select
          id="audit-filter"
          className="input w-64 py-1"
          value={action}
          onChange={(e) => { setAction(e.target.value); setPage(1); }}
        >
          <option value="">All actions</option>
          {actionsSeen.map((a) => <option key={a} value={a}>{label(a)}</option>)}
        </select>
        <span className="text-xs text-slate-400">{total} event{total === 1 ? "" : "s"}</span>
      </div>

      <DataTable
        caption="Workspace audit events"
        columns={columns}
        rows={events.data?.items}
        rowKey={(e) => e.id}
        loading={events.isLoading}
        empty={<EmptyState title="No events yet" description="Actions in this workspace will appear here as they happen." />}
      />

      {pages > 1 && (
        <div className="flex items-center justify-between text-sm">
          <Button variant="secondary" size="sm" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
            Previous
          </Button>
          <span className="text-xs text-slate-400">Page {page} of {pages}</span>
          <Button variant="secondary" size="sm" disabled={page >= pages} onClick={() => setPage((p) => Math.min(pages, p + 1))}>
            Next
          </Button>
        </div>
      )}
    </div>
  );
}
