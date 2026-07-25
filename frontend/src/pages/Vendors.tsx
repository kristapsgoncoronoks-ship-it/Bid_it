import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { Badge, Button, Card, ConfirmDialog, EmptyState, QueryState, Skeleton } from "../components/ui";
import { api, apiError } from "../lib/api";
import type { Vendor, VendorChangeRequest } from "../lib/types";

/** Mask an IBAN for display: last 4 characters only. The server already keeps
 * full IBANs out of the audit trail; this is screen hygiene, not security. */
function maskIban(value: string | null): string {
  if (!value) return "—";
  const v = value.replace(/\s+/g, "");
  return `…${v.slice(-4)} (${v.length})`;
}

function maskValue(field: string, value: string | null): string {
  return field === "iban" ? maskIban(value) : value ?? "—";
}

export default function VendorsPage() {
  const qc = useQueryClient();
  const { user } = useAuth();
  const [err, setErr] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<string, { iban: string; bic: string }>>({});
  const [confirming, setConfirming] = useState<VendorChangeRequest | null>(null);

  const vendors = useQuery<Vendor[]>({
    queryKey: ["vendors"],
    queryFn: async () => (await api.get("/vendors")).data,
  });
  const changes = useQuery<VendorChangeRequest[]>({
    queryKey: ["vendor-changes"],
    queryFn: async () => (await api.get("/vendors/changes")).data,
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["vendors"] });
    qc.invalidateQueries({ queryKey: ["vendor-changes"] });
  };

  const save = useMutation({
    mutationFn: async ({ v, iban, bic }: { v: Vendor; iban: string; bic: string }) =>
      (
        await api.patch(`/vendors/${v.id}`, {
          iban: iban || null,
          bic: bic || null,
          version: v.version ?? null,
        })
      ).data as Vendor,
    onSuccess: (data, { v }) => {
      setErr(null);
      setNotice(
        (data.pending_changes ?? []).some((c) => c.status === "pending")
          ? "Bank-detail change recorded as a pending request — a second approver must apply it."
          : null,
      );
      setDraft((d) => {
        const next = { ...d };
        delete next[v.id];
        return next;
      });
      refresh();
    },
    onError: (e) => setErr(apiError(e)),
  });

  const decide = useMutation({
    mutationFn: async ({ id, action }: { id: string; action: "approve" | "reject" }) =>
      (await api.post(`/vendors/changes/${id}/${action}`, {})).data,
    onSuccess: () => {
      setErr(null);
      setConfirming(null);
      refresh();
    },
    onError: (e) => {
      setErr(apiError(e));
      setConfirming(null);
    },
  });

  const val = (v: Vendor, key: "iban" | "bic") => draft[v.id]?.[key] ?? (v[key] ?? "");
  const set = (v: Vendor, key: "iban" | "bic", value: string) =>
    setDraft({
      ...draft,
      [v.id]: {
        iban: key === "iban" ? value : val(v, "iban"),
        bic: key === "bic" ? value : val(v, "bic"),
      },
    });

  const input = "rounded-lg border border-slate-300 px-2 py-1 text-sm";
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Suppliers</h1>
        <p className="text-slate-500">
          Bank details used when a payment run is exported as a SEPA credit-transfer file.
          Changing a stored IBAN or tax id needs a second approver.
        </p>
      </div>

      {err && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-700">
          {err}
        </div>
      )}
      {notice && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-700">
          {notice}
        </div>
      )}

      <Card>
        <h2 className="mb-2 text-sm font-semibold text-slate-600">Pending bank-detail changes</h2>
        <QueryState
          query={changes}
          loading={<Skeleton className="h-16 w-full" />}
          isEmpty={(rows) => rows.length === 0}
          empty={
            <EmptyState
              title="No pending changes"
              description="Requests to change a supplier's IBAN or tax id will appear here for a second approver."
            />
          }
          errorTitle="Couldn’t load pending changes"
        >
          {(rows) => (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-slate-400">
                  <th className="py-1">Supplier</th>
                  <th className="py-1">Field</th>
                  <th className="py-1">Change</th>
                  <th className="py-1">Requested by</th>
                  <th className="py-1">Source</th>
                  <th className="py-1"></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((c) => {
                  const mine = user?.id === c.requested_by;
                  return (
                    <tr key={c.id} className="border-t border-slate-100">
                      <td className="py-1 font-medium">{c.vendor_name ?? c.vendor_id}</td>
                      <td className="py-1 uppercase text-xs text-slate-500">{c.field}</td>
                      <td className="py-1 font-mono text-xs">
                        {maskValue(c.field, c.old_value)} → {maskValue(c.field, c.new_value)}
                      </td>
                      <td className="py-1">{c.requested_by_email ?? c.requested_by}</td>
                      <td className="py-1 text-xs text-slate-500">
                        {c.source_document_id ? (
                          <span className="font-mono" title="Vaulted source document id">
                            doc {c.source_document_id.slice(0, 8)}…
                          </span>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td className="py-1 text-right">
                        {mine ? (
                          <span className="text-xs text-slate-400">
                            Yours — a different approver must decide
                          </span>
                        ) : (
                          <Button
                            size="sm"
                            onClick={() => setConfirming(c)}
                            loading={decide.isPending && confirming?.id === c.id}
                          >
                            Approve
                          </Button>
                        )}{" "}
                        <Button
                          size="sm"
                          variant="secondary"
                          loading={decide.isPending}
                          onClick={() => decide.mutate({ id: c.id, action: "reject" })}
                        >
                          Reject
                        </Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </QueryState>
      </Card>

      <Card>
        <QueryState
          query={vendors}
          loading={<Skeleton className="h-24 w-full" />}
          isEmpty={(rows) => rows.length === 0}
          empty={
            <EmptyState
              title="No suppliers yet"
              description="They appear once you receive their invoices."
            />
          }
          errorTitle="Couldn’t load suppliers"
        >
          {(rows) => (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-slate-400">
                  <th className="py-1">Supplier</th>
                  <th className="py-1">Status</th>
                  <th className="py-1">IBAN</th>
                  <th className="py-1">BIC</th>
                  <th className="py-1"></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((v) => (
                  <tr key={v.id} className="border-t border-slate-100">
                    <td className="py-1 font-medium">{v.name}</td>
                    <td className="py-1">
                      {v.status === "provisional" ? (
                        <Badge tone="warning">provisional</Badge>
                      ) : (v.pending_changes ?? []).length > 0 ? (
                        <Badge tone="warning">change pending</Badge>
                      ) : (
                        <Badge tone="success">active</Badge>
                      )}
                    </td>
                    <td className="py-1">
                      <input
                        className={`${input} w-56 font-mono`}
                        placeholder="—"
                        value={val(v, "iban")}
                        onChange={(e) => set(v, "iban", e.target.value)}
                      />
                    </td>
                    <td className="py-1">
                      <input
                        className={`${input} w-32`}
                        placeholder="—"
                        value={val(v, "bic")}
                        onChange={(e) => set(v, "bic", e.target.value)}
                      />
                    </td>
                    <td className="py-1 text-right">
                      <Button
                        size="sm"
                        variant="secondary"
                        disabled={!draft[v.id]}
                        loading={save.isPending}
                        onClick={() => save.mutate({ v, iban: val(v, "iban"), bic: val(v, "bic") })}
                      >
                        Save
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </QueryState>
      </Card>

      <ConfirmDialog
        open={confirming !== null}
        onClose={() => setConfirming(null)}
        onConfirm={() => confirming && decide.mutate({ id: confirming.id, action: "approve" })}
        title="Apply this bank-detail change?"
        confirmLabel="Approve change"
        tone="danger"
        loading={decide.isPending}
      >
        {confirming && (
          <p className="text-sm text-slate-600">
            <span className="font-medium">{confirming.vendor_name}</span>: {confirming.field}{" "}
            <span className="font-mono">
              {maskValue(confirming.field, confirming.old_value)} →{" "}
              {maskValue(confirming.field, confirming.new_value)}
            </span>
            . Future payments to this supplier will use the new value. Verify it against the
            source document before approving.
          </p>
        )}
      </ConfirmDialog>
    </div>
  );
}
