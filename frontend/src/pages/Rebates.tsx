import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { ModuleInactive } from "../components/ModuleGate";
import { RefusalNotice } from "../components/RefusalNotice";
import {
  Badge,
  Button,
  Card,
  DateInput,
  EmptyState,
  PageHeader,
  QueryState,
  Skeleton,
  TextInput,
} from "../components/ui";
import { api, apiError, apiErrorCode } from "../lib/api";
import { decimalMoney, shortDate } from "../lib/format";
import { hasVatPerm } from "../lib/roles";
import { isDecimalShape, isPeriodShape } from "../lib/transportRecovery";
import { useModules } from "../lib/useModules";
import type { OffInvoiceRebate } from "../lib/types";

/**
 * The off-invoice rebate registry (WO-86) over the WO-84 routes.
 *
 * Some suppliers invoice at LIST price per country and pay the agreed discount
 * back on a SEPARATE rebate invoice, issued by a different party. No fuel-card
 * statement parser ever sees that second document, so unless it is recorded
 * here the platform prices those litres at list — and a supplier-overcharge
 * demand built on a list price asks for money the supplier has already paid.
 * Recording the document is the only way a rebate euro is ever allowed to reach
 * the effective net price.
 *
 * TWO BOUNDARIES THIS SCREEN MUST STATE PLAINLY, BECAUSE BOTH ARE SURPRISING:
 *
 * 1. **Recording a rebate changes no figure until the close runs.** The
 *    recompute of a transaction's effective net is a CLOSE stage, not a web
 *    request: an already-validated transaction's derived figure is product data
 *    the engine owns. There is therefore no "merge now" verb on this route and
 *    none is invented here, and — importantly — **no preview is faked**. WO-84
 *    shipped no preview endpoint, so this page says the effect applies at the
 *    next close and shows nothing that pretends to compute it early (§10).
 *
 * 2. **A rebate that cannot reach EUR is refused, not stored.** The form has no
 *    EUR field at all: `RebateIn` carries none, and the server resolves the euro
 *    through the ECB rate at the DOCUMENT's own date (§4.10). With no rate for
 *    that date the whole POST refuses `fx_rate_unavailable` and writes nothing —
 *    never a guessed number, and never a half-recorded row (§4.15). The refusal
 *    copy says exactly that, because "it failed" and "it silently stored
 *    something approximate" are the two readings an operator must be able to
 *    tell apart.
 *
 * Permissions are mirrored, not enforced: the list is `transport.read`, the
 * recording verb is `vat.write` — exactly how `rebates.py` declares them. A
 * hidden form is never a security boundary; the server is the control.
 *
 * No arithmetic anywhere: `amount_local` is posted as the typed string and
 * `amount_eur` is rendered as the server resolved it.
 */

const EMPTY = {
  supplier: "",
  country: "",
  period: "",
  source_ref: "",
  source_party: "",
  rebate_date: "",
  currency: "EUR",
  amount_local: "",
  note: "",
};

/** The FX provenance of a stored rebate. There is deliberately no "unknown"
 * branch: a rebate that could not be converted was refused rather than stored,
 * so an unknown provenance cannot exist in this table. */
const FX_SOURCE_COPY: Record<string, string> = {
  eur: "Already EUR — no conversion, no rate involved.",
  ecb: "Converted at the ECB reference rate for the document's own date.",
};

export default function RebatesPage() {
  const qc = useQueryClient();
  const { user } = useAuth();
  const modules = useModules();
  const canWrite = hasVatPerm(user, "vat.write"); // cosmetic — the server enforces it
  const [form, setForm] = useState(EMPTY);
  const [refusal, setRefusal] = useState<{
    code: string | null;
    detail: string;
  } | null>(null);
  const [recorded, setRecorded] = useState<OffInvoiceRebate | null>(null);

  const enabled = modules.isEnabled("transport");

  const rebates = useQuery<OffInvoiceRebate[]>({
    queryKey: ["transport", "rebates"],
    queryFn: async () => (await api.get("/transport/rebates")).data,
    enabled,
    retry: false,
  });

  const record = useMutation({
    mutationFn: async () =>
      (
        await api.post("/transport/rebates", {
          supplier: form.supplier.trim(),
          country: form.country.trim().toUpperCase(),
          period: form.period.trim(),
          source_ref: form.source_ref.trim(),
          source_party: form.source_party.trim(),
          rebate_date: form.rebate_date,
          currency: form.currency.trim().toUpperCase(),
          amount_local: form.amount_local.trim(),
          ...(form.note.trim() ? { note: form.note.trim() } : {}),
        })
      ).data as OffInvoiceRebate,
    onSuccess: (row) => {
      setRefusal(null);
      setRecorded(row);
      setForm({ ...EMPTY });
      qc.invalidateQueries({ queryKey: ["transport", "rebates"] });
    },
    onError: (e) => {
      setRecorded(null);
      setRefusal({ code: apiErrorCode(e), detail: apiError(e) });
    },
  });

  if (modules.isLoading) return <Skeleton className="h-24 w-full" />;
  if (!enabled) {
    return (
      <div className="space-y-6">
        <PageHeader title="Off-invoice rebates" />
        <ModuleInactive name={"Transport & VAT refunds"} />
      </div>
    );
  }

  const formReady =
    form.supplier.trim() !== "" &&
    form.country.trim().length === 2 &&
    isPeriodShape(form.period) &&
    form.source_ref.trim() !== "" &&
    form.source_party.trim() !== "" &&
    form.rebate_date !== "" &&
    form.currency.trim().length === 3 &&
    isDecimalShape(form.amount_local);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Off-invoice rebates"
        description="The second discount tier: a rebate paid back on its own document rather than deducted on the fuel invoice. Recording it is what lets the platform price those litres at the real effective net."
      />

      {refusal && (
        <RefusalNotice
          code={refusal.code}
          detail={refusal.detail}
          onDismiss={() => setRefusal(null)}
          periodShape="month"
        />
      )}

      {recorded && (
        <div
          role="status"
          className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900"
        >
          <p className="font-semibold">
            Recorded {recorded.source_ref} —{" "}
            {decimalMoney(recorded.amount_eur, "EUR")}
          </p>
          <p className="mt-1 text-emerald-800">
            No transaction figure has changed yet. The effective net price is
            recomputed by the monthly close, so this rebate applies at the next
            close for {recorded.period}.
          </p>
        </div>
      )}

      {/* The §3.H boundary, stated before the form rather than after a
          surprise. There is no preview here because the backend has no preview
          endpoint — inventing one would mean this page computing a money figure
          the engine owns. */}
      <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
        <p className="font-medium text-slate-700">
          Recording a rebate does not change any figure until the close runs
        </p>
        <p className="mt-1">
          This registry stores the rebate document. The effective net price on
          the fuel transactions it adjusts is recomputed by the monthly close,
          which is the only thing allowed to write a validated transaction's
          derived figure — so there is no “apply now” here, and no preview of
          the result: the number does not exist until the close computes it.
          Until then, a contract audit for that supplier and period is still
          reading the list price, and{" "}
          <Link to="/overcharges" className="font-medium underline">
            supplier overcharges
          </Link>{" "}
          will say so.
        </p>
      </div>

      {canWrite && (
        <Card title="Record a rebate document">
          <div className="grid gap-4 sm:grid-cols-3 lg:grid-cols-4">
            <TextInput
              label="Supplier"
              hint="The fuel-card or toll-network code"
              required
              value={form.supplier}
              onChange={(e) => setForm({ ...form, supplier: e.target.value })}
            />
            <TextInput
              label="Country"
              hint="ISO code, e.g. LV"
              required
              maxLength={2}
              value={form.country}
              onChange={(e) => setForm({ ...form, country: e.target.value })}
            />
            <TextInput
              label="Period adjusted"
              hint="YYYY-MM — the fuel month, not the document's month"
              required
              value={form.period}
              onChange={(e) => setForm({ ...form, period: e.target.value })}
              placeholder="2026-05"
            />
            <DateInput
              label="Document date"
              hint="The rebate document's own date — what the FX rate is taken at"
              required
              value={form.rebate_date}
              onValueChange={(v) => setForm({ ...form, rebate_date: v })}
            />
            <TextInput
              label="Document number"
              required
              value={form.source_ref}
              onChange={(e) => setForm({ ...form, source_ref: e.target.value })}
            />
            <TextInput
              label="Issued by"
              hint="The rebate partner, often not the fuel supplier"
              required
              value={form.source_party}
              onChange={(e) =>
                setForm({ ...form, source_party: e.target.value })
              }
            />
            <TextInput
              label="Currency"
              hint="Three-letter ISO code"
              required
              maxLength={3}
              value={form.currency}
              onChange={(e) => setForm({ ...form, currency: e.target.value })}
            />
            <TextInput
              label="Amount on the document"
              hint="In the currency above"
              required
              value={form.amount_local}
              onChange={(e) =>
                setForm({ ...form, amount_local: e.target.value })
              }
              placeholder="0.00"
            />
            <TextInput
              label="Note"
              hint="Optional"
              value={form.note}
              onChange={(e) => setForm({ ...form, note: e.target.value })}
              className="sm:col-span-2"
            />
            <div className="flex items-end">
              <Button
                onClick={() => record.mutate()}
                loading={record.isPending}
                disabled={!formReady}
              >
                Record rebate
              </Button>
            </div>
          </div>
          <p className="mt-3 text-xs text-slate-400">
            There is no euro field: the amount is stated in the currency the
            document was issued in, and the server converts it at the ECB rate
            for the document's own date. If no rate exists for that date the
            rebate is refused and nothing is recorded — a rebate is never stored
            at a guessed rate. Re-posting the same document number for the same
            supplier, country and period corrects that document in place.
          </p>
        </Card>
      )}

      <Card title="Recorded rebate documents" padded={false}>
        <QueryState
          query={rebates}
          loading={<Skeleton className="m-5 h-24 w-full" />}
          isEmpty={(rows) => rows.length === 0}
          empty={
            <EmptyState
              title="No rebate documents recorded"
              description={
                canWrite
                  ? "Until a rebate is recorded, the litres it covers are priced at list — and any overcharge demand built on them could ask for money already paid."
                  : "Nobody has recorded an off-invoice rebate for this workspace yet."
              }
            />
          }
          errorTitle="Couldn’t load the recorded rebates"
        >
          {(rows) => (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <caption className="sr-only">
                  Recorded off-invoice rebate documents
                </caption>
                <thead>
                  <tr className="text-left text-xs text-slate-400">
                    <th className="px-5 py-2">Supplier</th>
                    <th className="px-5 py-2">Country</th>
                    <th className="px-5 py-2">Period adjusted</th>
                    <th className="px-5 py-2">Document</th>
                    <th className="px-5 py-2">Issued by</th>
                    <th className="px-5 py-2">Document date</th>
                    <th className="px-5 py-2 text-right">On the document</th>
                    <th className="px-5 py-2 text-right">In EUR</th>
                    <th className="px-5 py-2">Rate used</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.id} className="border-t border-slate-100">
                      <td className="px-5 py-2">{r.supplier}</td>
                      <td className="px-5 py-2 font-mono text-xs">
                        {r.country}
                      </td>
                      <td className="px-5 py-2 font-mono text-xs">
                        {r.period}
                      </td>
                      <td className="px-5 py-2 font-mono text-xs">
                        {r.source_ref}
                      </td>
                      <td className="px-5 py-2 text-xs text-slate-500">
                        {r.source_party}
                      </td>
                      <td className="px-5 py-2 text-xs text-slate-500">
                        {shortDate(r.rebate_date)}
                      </td>
                      <td className="px-5 py-2 text-right tabular-nums">
                        {decimalMoney(r.amount_local, r.currency)}
                      </td>
                      <td className="px-5 py-2 text-right font-medium tabular-nums">
                        {decimalMoney(r.amount_eur, "EUR")}
                      </td>
                      <td className="px-5 py-2">
                        {r.fx_source ? (
                          <>
                            <Badge
                              tone={r.fx_source === "eur" ? "neutral" : "info"}
                            >
                              {r.fx_source}
                            </Badge>
                            <span className="mt-0.5 block text-xs text-slate-400">
                              {FX_SOURCE_COPY[r.fx_source] ?? ""}
                              {r.fx_ecb_rate && r.fx_ecb_date
                                ? ` ${r.fx_ecb_rate} on ${r.fx_ecb_date}.`
                                : ""}
                            </span>
                          </>
                        ) : (
                          <span className="text-slate-400">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </QueryState>
        <p className="border-t border-slate-100 px-5 py-3 text-xs text-slate-400">
          The document amount and its EUR value are shown side by side and are
          never added together — they are one figure in two currencies, not two
          figures.
        </p>
      </Card>
    </div>
  );
}
