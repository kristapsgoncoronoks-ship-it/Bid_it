import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Badge, Button, Card, Timeline, type Tone } from "../components/ui";
import { api, apiError } from "../lib/api";
import { money, shortDate } from "../lib/format";

// --- shapes (mirrors app/schemas/approval.py:ReviewOut) ----------------------
interface Recon {
  computed_subtotal: string;
  computed_tax: string;
  computed_total: string;
  stated_total: string;
  balanced: boolean;
  findings: string[];
}
interface Step {
  id: string;
  seq: number;
  kind: string;
  approver_email: string | null;
  status: string;
  decided_by_email: string | null;
  decided_at: string | null;
  note: string | null;
}
interface Comment {
  id: string;
  author_name: string | null;
  body: string;
  created_at: string;
}
interface Attachment {
  id: string;
  filename: string;
  size: number;
  note: string | null;
  uploaded_by_email: string | null;
  created_at: string;
}
interface Line {
  id?: string;
  description: string;
  category?: string;
  quantity: string;
  unit_price: string;
  amount?: string;
  tax_rate: string;
}
interface Review {
  id: string;
  invoice_number: string;
  vendor_name: string;
  currency: string;
  issue_date: string;
  due_date: string | null;
  status: string;
  workflow_state: string;
  workflow_label: string;
  version: number;
  locked: boolean;
  editable: boolean;
  allowed_targets: string[];
  subtotal: string;
  tax_amount: string;
  total: string;
  total_eur: string | null;
  account_code: string | null;
  cost_center: string | null;
  department: string | null;
  project: string | null;
  source_filename: string | null;
  notes: string | null;
  lines: Line[];
  reconciliation: Recon;
  approval_steps: Step[];
  comments: Comment[];
  attachments: Attachment[];
}
interface History {
  steps: Step[];
  events: { action: string; actor: string | null; at_ms: number; meta: string | null }[];
}

const STATE_TONE: Record<string, Tone> = {
  draft: "neutral",
  review_required: "warning",
  submitted: "info",
  partially_approved: "info",
  approved: "success",
  rejected: "danger",
  scheduled_for_payment: "brand",
  partially_paid: "brand",
  paid: "success",
  disputed: "warning",
  cancelled: "neutral",
  archived: "neutral",
  uploaded: "neutral",
  processing: "neutral",
};
const STEP_TONE: Record<string, Tone> = {
  pending: "info",
  approved: "success",
  rejected: "danger",
  skipped: "neutral",
};
const TARGET_LABEL: Record<string, string> = {
  scheduled_for_payment: "Schedule payment",
  partially_paid: "Mark partially paid",
  paid: "Mark paid",
  disputed: "Dispute",
  cancelled: "Cancel",
  archived: "Archive",
  draft: "Reopen for correction",
};
const APPROVAL_STATES = ["submitted", "partially_approved"];

export default function ReviewInvoicePage() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState("");

  const { data: inv, isLoading } = useQuery<Review>({
    queryKey: ["review", id],
    queryFn: async () => (await api.get(`/invoices/${id}/review`)).data,
    enabled: !!id,
  });
  const { data: hist } = useQuery<History>({
    queryKey: ["approvals", id],
    queryFn: async () => (await api.get(`/invoices/${id}/approvals`)).data,
    enabled: !!id,
  });

  const invalidate = () => {
    setErr(null);
    qc.invalidateQueries({ queryKey: ["review", id] });
    qc.invalidateQueries({ queryKey: ["approvals", id] });
    qc.invalidateQueries({ queryKey: ["invoices"] });
  };
  const onErr = (e: unknown) => setErr(apiError(e));

  // Workflow-action mutations (each carries the version it read → 409 on stale).
  const submit = useMutation({
    mutationFn: async () => (await api.post(`/invoices/${id}/submit`, { version: inv!.version })).data,
    onSuccess: invalidate,
    onError: onErr,
  });
  const decide = useMutation({
    mutationFn: async (kind: "approve" | "reject" | "return") =>
      (await api.post(`/invoices/${id}/${kind}`, { version: inv!.version, note: note || null })).data,
    onSuccess: () => {
      setNote("");
      invalidate();
    },
    onError: onErr,
  });
  const transition = useMutation({
    mutationFn: async (target: string) =>
      (await api.post(`/invoices/${id}/transition`, { version: inv!.version, target, note: note || null }))
        .data,
    onSuccess: () => {
      setNote("");
      invalidate();
    },
    onError: onErr,
  });

  if (isLoading || !inv) return <div className="text-slate-400">Loading…</div>;
  const s = inv.workflow_state;

  return (
    <div className="space-y-6">
      <Link to="/invoices" className="text-sm text-brand-600 hover:underline">
        ← Back to invoices
      </Link>

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">{inv.invoice_number}</h1>
          <p className="text-slate-500">{inv.vendor_name}</p>
        </div>
        <div className="flex items-center gap-2">
          <Badge tone={STATE_TONE[s] ?? "neutral"}>{inv.workflow_label}</Badge>
          {inv.locked && <Badge tone="neutral">🔒 Locked</Badge>}
          <span className="text-xs text-slate-400 tabular-nums">v{inv.version}</span>
        </div>
      </div>

      {err && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-700">
          {err}{" "}
          <button className="underline" onClick={invalidate}>
            reload
          </button>
        </div>
      )}

      {/* Reconciliation banner */}
      <div
        className={`rounded-lg border px-4 py-2 text-sm ${
          inv.reconciliation.balanced
            ? "border-emerald-200 bg-emerald-50 text-emerald-700"
            : "border-amber-200 bg-amber-50 text-amber-800"
        }`}
      >
        {inv.reconciliation.balanced ? (
          <>✓ Totals reconcile — {money(inv.total, inv.currency)} ({money(inv.subtotal, inv.currency)} + {money(inv.tax_amount, inv.currency)} tax)</>
        ) : (
          <>
            ⚠ Does not reconcile:
            <ul className="ml-4 list-disc">
              {inv.reconciliation.findings.map((f, i) => (
                <li key={i}>{f}</li>
              ))}
            </ul>
          </>
        )}
      </div>

      {/* Action bar */}
      <div className="flex flex-wrap items-center gap-2">
        {s === "draft" && (
          <Button
            variant="primary"
            loading={submit.isPending}
            disabled={!inv.reconciliation.balanced || inv.lines.length === 0}
            onClick={() => submit.mutate()}
          >
            Submit for approval
          </Button>
        )}
        {APPROVAL_STATES.includes(s) && (
          <>
            <Button variant="primary" loading={decide.isPending} onClick={() => decide.mutate("approve")}>
              Approve
            </Button>
            <Button variant="danger" onClick={() => decide.mutate("reject")}>
              Reject
            </Button>
            <Button variant="secondary" onClick={() => decide.mutate("return")}>
              Return for correction
            </Button>
          </>
        )}
        {inv.allowed_targets
          .filter((t) => t in TARGET_LABEL && !(APPROVAL_STATES.includes(s) && t === "draft"))
          .map((t) => (
            <Button
              key={t}
              variant={t === "cancelled" || t === "disputed" ? "secondary" : "subtle"}
              loading={transition.isPending}
              onClick={() => transition.mutate(t)}
            >
              {TARGET_LABEL[t]}
            </Button>
          ))}
        {(APPROVAL_STATES.includes(s) || inv.allowed_targets.length > 0) && (
          <input
            className="ml-auto w-64 rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
            placeholder="Note (optional)…"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        )}
      </div>

      {/* Side-by-side: document | data */}
      <div className="grid gap-6 lg:grid-cols-2">
        <DocumentPanel inv={inv} onErr={onErr} refresh={invalidate} />
        <div className="space-y-6">
          {inv.editable ? (
            <EditPanel inv={inv} onErr={onErr} refresh={invalidate} />
          ) : (
            <ReadonlyData inv={inv} />
          )}
          <CommentsPanel inv={inv} refresh={invalidate} onErr={onErr} />
        </div>
      </div>

      {/* Approval history */}
      <Card>
        <h2 className="mb-3 text-sm font-semibold text-slate-700">Approval history</h2>
        <Timeline
          events={[
            ...inv.approval_steps.map((st) => ({
              id: st.id,
              title: `Step ${st.seq + 1} — ${st.approver_email ?? "any approver"}`,
              actor: st.decided_by_email ?? undefined,
              timestamp: st.decided_at ? shortDate(st.decided_at) : "pending",
              detail: st.note ?? undefined,
              tone: STEP_TONE[st.status] ?? "neutral",
            })),
            ...(hist?.events ?? []).map((e, i) => ({
              id: `e${i}`,
              title: e.action.replace("invoice.", "").replace("_", " "),
              actor: e.actor ?? undefined,
              timestamp: new Date(e.at_ms).toLocaleString(),
              tone: "neutral" as Tone,
            })),
          ]}
        />
        {inv.approval_steps.length === 0 && (hist?.events.length ?? 0) === 0 && (
          <p className="text-sm text-slate-400">Not yet submitted for approval.</p>
        )}
      </Card>
    </div>
  );
}

// --- document + attachments (left column) ------------------------------------
function DocumentPanel({
  inv,
  onErr,
  refresh,
}: {
  inv: Review;
  onErr: (e: unknown) => void;
  refresh: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const upload = useMutation({
    mutationFn: async () => {
      const fd = new FormData();
      fd.append("file", file!);
      return (await api.post(`/invoices/${inv.id}/attachments`, fd)).data;
    },
    onSuccess: () => {
      setFile(null);
      refresh();
    },
    onError: onErr,
  });
  return (
    <Card>
      <h2 className="mb-2 text-sm font-semibold text-slate-700">Source document</h2>
      <div className="flex h-40 items-center justify-center rounded-lg border border-dashed border-slate-300 bg-slate-50 text-sm text-slate-400">
        {inv.source_filename ?? "No source file"}
      </div>
      <h3 className="mb-2 mt-5 text-sm font-semibold text-slate-700">Internal attachments</h3>
      <ul className="space-y-1 text-sm">
        {inv.attachments.map((a) => (
          <li key={a.id} className="flex items-center justify-between">
            <a
              className="text-brand-600 hover:underline"
              href={`/api/v1/invoices/${inv.id}/attachments/${a.id}/download`}
            >
              {a.filename}
            </a>
            <span className="text-xs text-slate-400">{(a.size / 1024).toFixed(0)} KB</span>
          </li>
        ))}
        {inv.attachments.length === 0 && <li className="text-slate-400">None yet.</li>}
      </ul>
      <div className="mt-3 flex items-center gap-2">
        <input type="file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="text-xs" />
        <Button size="sm" disabled={!file} loading={upload.isPending} onClick={() => upload.mutate()}>
          Attach
        </Button>
      </div>
    </Card>
  );
}

// --- readonly data (locked states) -------------------------------------------
function ReadonlyData({ inv }: { inv: Review }) {
  return (
    <Card>
      <h2 className="mb-3 text-sm font-semibold text-slate-700">Extracted data</h2>
      <dl className="grid grid-cols-2 gap-y-2 text-sm">
        <dt className="text-slate-500">Issue date</dt>
        <dd>{shortDate(inv.issue_date)}</dd>
        <dt className="text-slate-500">Due date</dt>
        <dd>{shortDate(inv.due_date)}</dd>
        <dt className="text-slate-500">Account</dt>
        <dd>{inv.account_code ?? "—"}</dd>
        <dt className="text-slate-500">Department</dt>
        <dd>{inv.department ?? "—"}</dd>
        <dt className="text-slate-500">Cost center</dt>
        <dd>{inv.cost_center ?? "—"}</dd>
        <dt className="text-slate-500">Total</dt>
        <dd className="font-medium">{money(inv.total, inv.currency)}</dd>
      </dl>
      <table className="mt-4 w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-slate-400">
            <th className="py-1">Description</th>
            <th className="py-1 text-right">Qty</th>
            <th className="py-1 text-right">Unit</th>
            <th className="py-1 text-right">Tax %</th>
            <th className="py-1 text-right">Amount</th>
          </tr>
        </thead>
        <tbody>
          {inv.lines.map((l) => (
            <tr key={l.id} className="border-t border-slate-100">
              <td className="py-1">{l.description}</td>
              <td className="py-1 text-right tabular-nums">{l.quantity}</td>
              <td className="py-1 text-right tabular-nums">{l.unit_price}</td>
              <td className="py-1 text-right tabular-nums">{l.tax_rate}</td>
              <td className="py-1 text-right tabular-nums">{money(l.amount ?? "0", inv.currency)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

// --- editable header + lines (editable states) -------------------------------
function EditPanel({
  inv,
  onErr,
  refresh,
}: {
  inv: Review;
  onErr: (e: unknown) => void;
  refresh: () => void;
}) {
  const [form, setForm] = useState({
    invoice_number: inv.invoice_number,
    issue_date: inv.issue_date,
    due_date: inv.due_date ?? "",
    vendor_name: inv.vendor_name,
    account_code: inv.account_code ?? "",
    department: inv.department ?? "",
    cost_center: inv.cost_center ?? "",
    project: inv.project ?? "",
    notes: inv.notes ?? "",
  });
  const [lines, setLines] = useState<Line[]>(inv.lines);
  // Re-seed the local editors whenever a fresh version arrives.
  useEffect(() => {
    setLines(inv.lines);
  }, [inv.version, inv.lines]);

  const saveHeader = useMutation({
    mutationFn: async () =>
      (
        await api.patch(`/invoices/${inv.id}/review`, {
          version: inv.version,
          ...form,
          due_date: form.due_date || null,
        })
      ).data,
    onSuccess: refresh,
    onError: onErr,
  });
  const saveLines = useMutation({
    mutationFn: async () =>
      (await api.put(`/invoices/${inv.id}/lines`, { version: inv.version, lines })).data,
    onSuccess: refresh,
    onError: onErr,
  });

  const setLine = (i: number, k: keyof Line, v: string) =>
    setLines((ls) => ls.map((l, j) => (j === i ? { ...l, [k]: v } : l)));

  return (
    <Card>
      <h2 className="mb-3 text-sm font-semibold text-slate-700">Extracted data — review &amp; correct</h2>
      <div className="grid grid-cols-2 gap-3 text-sm">
        {(
          [
            ["invoice_number", "Invoice #"],
            ["vendor_name", "Supplier"],
            ["issue_date", "Issue date"],
            ["due_date", "Due date"],
            ["account_code", "Account / GL"],
            ["department", "Department"],
            ["cost_center", "Cost center"],
            ["project", "Project"],
          ] as [keyof typeof form, string][]
        ).map(([k, label]) => (
          <label key={k} className="flex flex-col gap-1">
            <span className="text-xs text-slate-500">{label}</span>
            <input
              className="rounded-lg border border-slate-300 px-3 py-1.5"
              value={form[k]}
              onChange={(e) => setForm({ ...form, [k]: e.target.value })}
            />
          </label>
        ))}
      </div>
      <div className="mt-3 flex justify-end">
        <Button size="sm" loading={saveHeader.isPending} onClick={() => saveHeader.mutate()}>
          Save header
        </Button>
      </div>

      <h3 className="mb-2 mt-5 text-sm font-semibold text-slate-700">Line items</h3>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-slate-400">
            <th className="py-1">Description</th>
            <th className="py-1 text-right">Qty</th>
            <th className="py-1 text-right">Unit</th>
            <th className="py-1 text-right">Tax %</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {lines.map((l, i) => (
            <tr key={i} className="border-t border-slate-100">
              <td className="py-1">
                <input
                  className="w-full rounded border border-slate-200 px-1.5 py-1"
                  value={l.description}
                  onChange={(e) => setLine(i, "description", e.target.value)}
                />
              </td>
              {(["quantity", "unit_price", "tax_rate"] as (keyof Line)[]).map((k) => (
                <td key={k} className="py-1">
                  <input
                    className="w-16 rounded border border-slate-200 px-1.5 py-1 text-right tabular-nums"
                    value={(l[k] as string) ?? ""}
                    onChange={(e) => setLine(i, k, e.target.value)}
                  />
                </td>
              ))}
              <td className="py-1 text-right">
                <button
                  className="text-xs text-rose-500 hover:underline"
                  onClick={() => setLines((ls) => ls.filter((_, j) => j !== i))}
                >
                  remove
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-2 flex items-center justify-between">
        <button
          className="text-xs text-brand-600 hover:underline"
          onClick={() =>
            setLines((ls) => [
              ...ls,
              { description: "", quantity: "1", unit_price: "0", tax_rate: "0" },
            ])
          }
        >
          + Add line
        </button>
        <Button size="sm" loading={saveLines.isPending} onClick={() => saveLines.mutate()}>
          Save lines
        </Button>
      </div>
    </Card>
  );
}

// --- comments ----------------------------------------------------------------
function CommentsPanel({
  inv,
  refresh,
  onErr,
}: {
  inv: Review;
  refresh: () => void;
  onErr: (e: unknown) => void;
}) {
  const [body, setBody] = useState("");
  const add = useMutation({
    mutationFn: async () => (await api.post(`/invoices/${inv.id}/comments`, { body })).data,
    onSuccess: () => {
      setBody("");
      refresh();
    },
    onError: onErr,
  });
  return (
    <Card>
      <h2 className="mb-3 text-sm font-semibold text-slate-700">Comments</h2>
      <ul className="space-y-2 text-sm">
        {inv.comments.map((c) => (
          <li key={c.id} className="rounded-lg bg-slate-50 px-3 py-2">
            <div className="flex justify-between text-xs text-slate-400">
              <span>{c.author_name ?? "—"}</span>
              <span>{shortDate(c.created_at)}</span>
            </div>
            <p className="text-slate-700">{c.body}</p>
          </li>
        ))}
        {inv.comments.length === 0 && <li className="text-slate-400">No comments yet.</li>}
      </ul>
      <div className="mt-3 flex gap-2">
        <input
          className="flex-1 rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
          placeholder="Add a comment…"
          value={body}
          onChange={(e) => setBody(e.target.value)}
        />
        <Button size="sm" disabled={!body.trim()} loading={add.isPending} onClick={() => add.mutate()}>
          Post
        </Button>
      </div>
    </Card>
  );
}
