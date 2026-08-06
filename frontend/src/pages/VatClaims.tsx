import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { ModuleInactive } from "../components/ModuleGate";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  PageHeader,
  QueryState,
  Select,
  Skeleton,
  StatusBadge,
  TextInput,
} from "../components/ui";
import { api, apiError, apiErrorCode } from "../lib/api";
import { decimalMoney, shortDate } from "../lib/format";
import { hasVatPerm } from "../lib/roles";
import { claimRefusal, isMappedRefusal } from "../lib/transportClaims";
import { useModules } from "../lib/useModules";
import type { IssuerProfile, VatClaim } from "../lib/types";

/**
 * The VAT refund claims workspace — list (WO-78, the M3 UI batch opener).
 *
 * Reads `GET /transport/claims` (VAT_READ) and creates on the R1 grain
 * `(entity, refund country, reference period)` via `POST /transport/claims`
 * (VAT_WRITE, idempotent — posting the same grain twice returns the same claim,
 * which is why the button says "Open or create").
 *
 * Money is rendered with `decimalMoney` straight off the wire string: the frozen
 * `vat_eur` is the SERVER's figure and this page neither sums nor re-rounds it
 * (master-context §4.9/§4.10). A null total is an em dash — "not frozen yet" is
 * a different fact from zero.
 *
 * Permission gating here is cosmetic (master-context §6): an operator without
 * VAT_WRITE simply doesn't see the create form, and the server still refuses.
 */

/** The refusal banner both claim pages use — the mapped sentence, the action to
 * take, and (when we mapped the code) the server's own detail as the specifics
 * it named. The raw slug is never shown. */
export function RefusalNotice({
  code,
  detail,
  onDismiss,
  action,
}: {
  code: string | null;
  detail: string;
  onDismiss?: () => void;
  action?: React.ReactNode;
}) {
  const refusal = claimRefusal(code, detail);
  return (
    <div
      role="alert"
      className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-semibold">{refusal.title}</p>
          {refusal.next && <p className="mt-1 text-rose-700">{refusal.next}</p>}
          {isMappedRefusal(code) && detail && (
            <p className="mt-1 text-xs text-rose-600">{detail}</p>
          )}
          {action && <div className="mt-3">{action}</div>}
        </div>
        {onDismiss && (
          <Button size="sm" variant="ghost" onClick={onDismiss}>
            Dismiss
          </Button>
        )}
      </div>
    </div>
  );
}

export default function VatClaimsPage() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { user } = useAuth();
  const modules = useModules();
  const canWrite = hasVatPerm(user, "vat.write"); // cosmetic — the server enforces it
  const [refusal, setRefusal] = useState<{ code: string | null; detail: string } | null>(null);
  const [form, setForm] = useState({ entity_id: "", refund_country: "", ref_period: "" });

  const enabled = modules.isEnabled("transport");

  const claims = useQuery<VatClaim[]>({
    queryKey: ["transport", "claims"],
    queryFn: async () => (await api.get("/transport/claims")).data,
    enabled,
  });

  // The legal entities a claim can be raised for. This is the shared issuer
  // registry (`GET /issuer/registry`, ISSUED_READ) — the same rows the transport
  // services key their `entity_id` to. It is a CONVENIENCE only: if it fails or
  // the caller can't read it, the page still lists claims and shows the raw
  // entity id, because no transport route serves entity names.
  const entities = useQuery<IssuerProfile[]>({
    queryKey: ["issuer", "registry"],
    queryFn: async () => (await api.get("/issuer/registry")).data,
    enabled,
    retry: false,
  });
  const entityName = (id: string) => {
    const found = entities.data?.find((e) => e.id === id);
    return found?.name || found?.legal_name || id;
  };

  const create = useMutation({
    mutationFn: async () =>
      (
        await api.post("/transport/claims", {
          entity_id: form.entity_id,
          refund_country: form.refund_country.trim().toUpperCase(),
          ref_period: form.ref_period.trim().toUpperCase(),
        })
      ).data as VatClaim,
    onSuccess: (claim) => {
      setRefusal(null);
      qc.invalidateQueries({ queryKey: ["transport", "claims"] });
      navigate(`/vat-claims/${claim.id}`);
    },
    onError: (e) => setRefusal({ code: apiErrorCode(e), detail: apiError(e) }),
  });

  if (modules.isLoading) return <Skeleton className="h-24 w-full" />;
  if (!enabled) {
    return (
      <div className="space-y-6">
        <PageHeader title="VAT refund claims" />
        <ModuleInactive name={"Transport & VAT refunds"} />
      </div>
    );
  }

  const formReady =
    form.entity_id.trim() !== "" &&
    form.refund_country.trim().length === 2 &&
    form.ref_period.trim() !== "";

  return (
    <div className="space-y-6">
      <PageHeader
        title="VAT refund claims"
        description="EU cross-border VAT refunds under Directive 2008/9/EC. One claim per legal entity, refunding country and reference period. Amounts are shown exactly as the service froze them."
      />

      {refusal && (
        <RefusalNotice
          code={refusal.code}
          detail={refusal.detail}
          onDismiss={() => setRefusal(null)}
        />
      )}

      {canWrite && (
        <Card title="Start a claim">
          <div className="grid gap-4 sm:grid-cols-4">
            {entities.data && entities.data.length > 0 ? (
              <Select
                label="Legal entity"
                required
                value={form.entity_id}
                onChange={(e) => setForm({ ...form, entity_id: e.target.value })}
              >
                <option value="">Choose…</option>
                {entities.data.map((e) => (
                  <option key={e.id} value={e.id}>
                    {e.name || e.legal_name || e.id}
                  </option>
                ))}
              </Select>
            ) : (
              <TextInput
                label="Legal entity"
                required
                value={form.entity_id}
                onChange={(e) => setForm({ ...form, entity_id: e.target.value })}
                placeholder="Entity id"
              />
            )}
            <TextInput
              label="Refunding country"
              hint="ISO code, e.g. LV"
              required
              value={form.refund_country}
              onChange={(e) => setForm({ ...form, refund_country: e.target.value })}
              maxLength={2}
              placeholder="LV"
            />
            <TextInput
              label="Reference period"
              hint="2026-Q2 or 2026-YEAR"
              required
              value={form.ref_period}
              onChange={(e) => setForm({ ...form, ref_period: e.target.value })}
              placeholder="2026-Q2"
            />
            <div className="flex items-end">
              <Button
                onClick={() => create.mutate()}
                loading={create.isPending}
                disabled={!formReady}
              >
                Open or create
              </Button>
            </div>
          </div>
          <p className="mt-3 text-xs text-slate-400">
            Opening the same entity, country and period again returns the claim that already
            exists — it never creates a second one.
          </p>
        </Card>
      )}

      <Card padded={false}>
        <QueryState
          query={claims}
          loading={<Skeleton className="m-5 h-24 w-full" />}
          isEmpty={(rows) => rows.length === 0}
          empty={
            <EmptyState
              title="No VAT refund claims yet"
              description={
                canWrite
                  ? "Start one above: pick the legal entity, the country you are reclaiming from, and the period it covers."
                  : "Nobody has started a refund claim for this workspace yet."
              }
            />
          }
          errorTitle="Couldn’t load VAT claims"
        >
          {(rows) => (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <caption className="sr-only">VAT refund claims</caption>
                <thead>
                  <tr className="text-left text-xs text-slate-400">
                    <th className="px-5 py-2">Entity</th>
                    <th className="px-5 py-2">Refund country</th>
                    <th className="px-5 py-2">Period</th>
                    <th className="px-5 py-2">Status</th>
                    <th className="px-5 py-2">Stage</th>
                    <th className="px-5 py-2 text-right">Frozen VAT</th>
                    <th className="px-5 py-2">Created</th>
                    <th className="px-5 py-2"></th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((claim) => (
                    <tr key={claim.id} className="border-t border-slate-100">
                      <td className="px-5 py-2">{entityName(claim.entity_id)}</td>
                      <td className="px-5 py-2 font-mono text-xs">{claim.refund_country}</td>
                      <td className="px-5 py-2 font-mono text-xs">{claim.ref_period}</td>
                      <td className="px-5 py-2">
                        <StatusBadge status={claim.status} />
                      </td>
                      <td className="px-5 py-2">
                        {claim.status_code ? (
                          <Badge tone="info">{claim.status_code}</Badge>
                        ) : (
                          <span className="text-slate-400">—</span>
                        )}
                      </td>
                      <td className="px-5 py-2 text-right font-medium tabular-nums">
                        {decimalMoney(claim.vat_eur)}
                      </td>
                      <td className="px-5 py-2 text-xs text-slate-500">
                        {shortDate(claim.created_at)}
                      </td>
                      <td className="px-5 py-2 text-right">
                        <Link
                          to={`/vat-claims/${claim.id}`}
                          className="text-sm font-medium text-brand-600 hover:underline"
                        >
                          Open
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </QueryState>
      </Card>
    </div>
  );
}
