import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { EntityPicker, entityLabel, useEntities } from "../components/EntityPicker";
import { ModuleInactive } from "../components/ModuleGate";
import { RefusalNotice } from "../components/RefusalNotice";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Modal,
  PageHeader,
  QueryState,
  Select,
  Skeleton,
  TabPanel,
  Tabs,
  TextInput,
} from "../components/ui";
import { api, apiError, apiErrorCode } from "../lib/api";
import { decimalMoney } from "../lib/format";
import { hasVatPerm } from "../lib/roles";
import {
  CADENCES,
  RECEIPT_STATUS_COPY,
  currentPeriod,
  isPeriodShape,
} from "../lib/transportAdmin";
import { useModules } from "../lib/useModules";
import type {
  InvoiceList,
  IssuerProfile,
  VatCadence,
  VatChecklistRule,
  VatNoteOverride,
  VatReceiptControl,
  VatStatusCodes,
  VatTieOutExpectation,
} from "../lib/types";

/**
 * The transport ADMIN/CONFIGURATION workspace (WO-80) — one tabbed page over
 * the WO-77 `api/routes/transport/admin.py` routes, which until now had no UI
 * at all.
 *
 * Six panels, each reading and writing exactly one service surface:
 *   1. Checklist rules      (R45)  — list · seed · per-rule active toggle
 *   2. Receipt control      (G3.5) — the persisted slot grid + the override
 *   3. Cadences             (G3.5) — the admin per-supplier assignments
 *   4. Note→invoice refs    (R16)  — list · set (NO delete: see below)
 *   5. Tie-out expectations (R25)  — list · upsert · delete
 *   6. Status codes         (R17)  — the vocabulary read (reference only)
 *
 * PERMISSIONS ARE MIRRORED, NOT ENFORCED HERE. Reads need VAT_READ; every
 * mutation on this page is VAT_WRITE, which is exactly how `admin.py` declares
 * them. An operator without VAT_WRITE sees no mutating control (a dead button
 * is worse than no button) and the server stays the only control — a 403 it
 * returns still renders through `RefusalNotice` (master-context §6).
 *
 * ADVISORY VS GATE — THE DISTINCTION THIS PAGE MUST NOT BLUR (§4.19):
 * the receipt-control grid is a CHASE LIST. `receipt_control.py` is explicit
 * that a `missing` slot never gates a claim or a close, and `waived` there is a
 * worklist mute, not the claim-level legal waiver (R15, which lives on the
 * claim). The tie-out expectations are the opposite: once typed they HALT the
 * close when the engine disagrees (R25 regime 2). Both facts are stated in the
 * panels' own copy, because a configuration screen that misrepresents which
 * lever blocks work is worse than no screen.
 *
 * WHAT THIS PAGE DELIBERATELY DOES NOT OFFER, AND WHY:
 * - No DELETE for a note→invoice override. `invoice_match` has no removal
 *   function — R16's harvested lifecycle is "de-register the target ⇒ CASCADE
 *   deletes the row" (WO-77 decision 6). A button with no route behind it would
 *   be invented functionality (§10).
 * - No "run receipt control" trigger. The run is the monthly close's
 *   `run_control` stage and R60 is explicit that the close never runs inline in
 *   a web request (WO-77 decision 7); this page serves what that run PERSISTED.
 * - No status LABELS. This codebase carries none (WO-77 decision 5) — the codes
 *   are the vocabulary, and the manual `set status-code` action is VAT_SUBMIT
 *   and already lives on the claim detail, so it is linked, never duplicated.
 * - No arithmetic on any figure. Amounts render through `decimalMoney` from the
 *   wire string and are posted back as the typed string; the service is the one
 *   place that quantizes (§4.9/§4.10).
 */

type TabKey = "rules" | "controls" | "cadences" | "overrides" | "tieout" | "codes";

const TABS: { value: TabKey; label: string }[] = [
  { value: "rules", label: "Checklist rules" },
  { value: "controls", label: "Receipt control" },
  { value: "cadences", label: "Cadences" },
  { value: "overrides", label: "Invoice references" },
  { value: "tieout", label: "Tie-out expectations" },
  { value: "codes", label: "Status codes" },
];

export interface PanelProps {
  canWrite: boolean;
  onRefusal: (e: unknown) => void;
  clearRefusal: () => void;
  entities: IssuerProfile[] | undefined;
}

export default function VatAdminPage() {
  const { user } = useAuth();
  const modules = useModules();
  const canWrite = hasVatPerm(user, "vat.write"); // cosmetic — the server enforces
  const [tab, setTab] = useState<TabKey>("rules");
  const [refusal, setRefusal] = useState<{ code: string | null; detail: string } | null>(null);

  const enabled = modules.isEnabled("transport");
  const entities = useEntities(enabled);

  const onRefusal = (e: unknown) => setRefusal({ code: apiErrorCode(e), detail: apiError(e) });
  const clearRefusal = () => setRefusal(null);

  if (modules.isLoading) return <Skeleton className="h-24 w-full" />;
  if (!enabled) {
    return (
      <div className="space-y-6">
        <PageHeader title="VAT refund configuration" />
        <ModuleInactive name={"Transport & VAT refunds"} />
      </div>
    );
  }

  const panel: PanelProps = { canWrite, onRefusal, clearRefusal, entities: entities.data };

  return (
    <div className="space-y-6">
      <PageHeader
        title="VAT refund configuration"
        description="How refund claims are built, checked and chased. Nothing here files or unfiles a claim — the claim workflow lives on each claim."
        actions={
          <Link to="/vat-claims" className="btn-ghost">
            Back to claims
          </Link>
        }
      />

      {refusal && (
        <RefusalNotice code={refusal.code} detail={refusal.detail} onDismiss={clearRefusal} />
      )}

      <Tabs
        tabs={TABS}
        value={tab}
        onChange={(v) => {
          clearRefusal();
          setTab(v as TabKey);
        }}
        label="VAT refund configuration sections"
        idBase="vat-admin"
      />

      {tab === "rules" && (
        <TabPanel idBase="vat-admin" value="rules">
          <RulesPanel {...panel} />
        </TabPanel>
      )}
      {tab === "controls" && (
        <TabPanel idBase="vat-admin" value="controls">
          <ControlsPanel {...panel} />
        </TabPanel>
      )}
      {tab === "cadences" && (
        <TabPanel idBase="vat-admin" value="cadences">
          <CadencesPanel {...panel} />
        </TabPanel>
      )}
      {tab === "overrides" && (
        <TabPanel idBase="vat-admin" value="overrides">
          <OverridesPanel {...panel} />
        </TabPanel>
      )}
      {tab === "tieout" && (
        <TabPanel idBase="vat-admin" value="tieout">
          <TieOutPanel {...panel} />
        </TabPanel>
      )}
      {tab === "codes" && (
        <TabPanel idBase="vat-admin" value="codes">
          <StatusCodesPanel />
        </TabPanel>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// 1. Checklist rules (R45)
// --------------------------------------------------------------------------- //

function RulesPanel({ canWrite, onRefusal, clearRefusal }: PanelProps) {
  const qc = useQueryClient();
  const rules = useQuery<VatChecklistRule[]>({
    queryKey: ["transport", "checklist-rules"],
    queryFn: async () => (await api.get("/transport/checklist-rules")).data,
  });

  const done = () => {
    clearRefusal();
    qc.invalidateQueries({ queryKey: ["transport", "checklist-rules"] });
  };
  const seed = useMutation({
    mutationFn: async () => (await api.post("/transport/checklist-rules/seed")).data,
    onSuccess: done,
    onError: onRefusal,
  });
  const toggle = useMutation({
    mutationFn: async (v: { key: string; active: boolean }) =>
      (await api.post(`/transport/checklist-rules/${v.key}/active`, { active: v.active })).data,
    onSuccess: done,
    onError: onRefusal,
  });

  return (
    <Card
      title="Submission checklist rules"
      padded={false}
      actions={
        canWrite && (
          <Button size="sm" variant="secondary" loading={seed.isPending} onClick={() => seed.mutate()}>
            Seed the default rules
          </Button>
        )
      }
    >
      <p className="px-5 pt-4 text-xs text-slate-400">
        These rules are what the claim checklist evaluates. Deactivating one removes it from every
        claim’s checklist. Reading a claim’s checklist never creates these rows — seeding is the
        only thing that does, which is why an empty table below is a first run, not a fault.
      </p>
      <QueryState
        query={rules}
        loading={<Skeleton className="m-5 h-24 w-full" />}
        isEmpty={(rows) => rows.length === 0}
        empty={
          <EmptyState
            title="No checklist rules in this workspace yet"
            description={
              canWrite
                ? "Seed the defaults to start from the standard set, then switch off anything that doesn’t apply."
                : "Someone with configuration rights needs to seed the default rules first."
            }
          />
        }
        errorTitle="Couldn’t load the checklist rules"
      >
        {(rows) => (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <caption className="sr-only">Submission checklist rules</caption>
              <thead>
                <tr className="text-left text-xs text-slate-400">
                  <th className="px-5 py-2">Rule</th>
                  <th className="px-5 py-2">Key</th>
                  <th className="px-5 py-2">Checked at</th>
                  <th className="px-5 py-2">State</th>
                  {canWrite && <th className="px-5 py-2"></th>}
                </tr>
              </thead>
              <tbody>
                {rows.map((rule) => (
                  <tr key={rule.id} className="border-t border-slate-100">
                    <td className="px-5 py-2 text-slate-700">{rule.label}</td>
                    <td className="px-5 py-2 font-mono text-xs text-slate-500">{rule.key}</td>
                    <td className="px-5 py-2 text-xs text-slate-500">
                      {rule.scope}
                      <span className="ml-1 text-slate-400">({rule.check_type})</span>
                    </td>
                    <td className="px-5 py-2">
                      <Badge tone={rule.active ? "success" : "neutral"}>
                        {rule.active ? "Active" : "Off"}
                      </Badge>
                    </td>
                    {canWrite && (
                      <td className="px-5 py-2 text-right">
                        <Button
                          size="sm"
                          variant="secondary"
                          loading={toggle.isPending && toggle.variables?.key === rule.key}
                          onClick={() => toggle.mutate({ key: rule.key, active: !rule.active })}
                        >
                          {rule.active ? "Switch off" : "Switch on"}
                        </Button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </QueryState>
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// 2. Receipt control — the persisted slot grid (G3.5). ADVISORY.
// --------------------------------------------------------------------------- //

function ControlsPanel({ canWrite, onRefusal, clearRefusal, entities }: PanelProps) {
  const qc = useQueryClient();
  const [period, setPeriod] = useState(currentPeriod());
  const [editing, setEditing] = useState<VatReceiptControl | null>(null);
  const [form, setForm] = useState({ waived: false, note: "" });

  const controls = useQuery<VatReceiptControl[]>({
    queryKey: ["transport", "receipt-controls", period],
    queryFn: async () =>
      (await api.get("/transport/receipt-controls", { params: { period } })).data,
    enabled: isPeriodShape(period),
  });

  const override = useMutation({
    mutationFn: async (v: { id: string; waived: boolean; note: string | null }) =>
      (
        await api.post(`/transport/receipt-controls/${v.id}/override`, {
          waived: v.waived,
          note: v.note,
        })
      ).data,
    onSuccess: () => {
      setEditing(null);
      clearRefusal();
      qc.invalidateQueries({ queryKey: ["transport", "receipt-controls"] });
    },
    onError: onRefusal,
  });

  const openEdit = (row: VatReceiptControl) => {
    setForm({ waived: row.waived, note: row.note ?? "" });
    setEditing(row);
  };

  return (
    <div className="space-y-4">
      <Card title="Receipt control — did each supplier invoice us?">
        <p className="text-xs text-slate-500">
          One row per supplier, slot and country for the period, exactly as the monthly close
          computed it. <strong>This board is a chase list.</strong> A slot showing “Chase the
          supplier” blocks no claim, halts no close and changes no figure — the gates that do stop
          a filing are the document check and the submission checklist, on the claim itself.
          Muting a slot here only takes it off this list; it is not the claim-level waiver.
        </p>
        <div className="mt-4 max-w-xs">
          <TextInput
            label="Period"
            hint="YYYY-MM — the accounting month the close computed."
            value={period}
            onChange={(e) => setPeriod(e.target.value)}
            placeholder="2026-05"
          />
        </div>
      </Card>

      <Card padded={false}>
        <QueryState
          query={controls}
          loading={<Skeleton className="m-5 h-24 w-full" />}
          isEmpty={(rows) => rows.length === 0}
          empty={
            <EmptyState
              title="Nothing recorded for this period"
              description="The grid is written by the monthly close. If the close hasn’t run for this period there is nothing to chase yet."
            />
          }
          errorTitle="Couldn’t load the receipt-control grid"
        >
          {(rows) => (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <caption className="sr-only">Receipt-control grid for {period}</caption>
                <thead>
                  <tr className="text-left text-xs text-slate-400">
                    <th className="px-5 py-2">Entity</th>
                    <th className="px-5 py-2">Supplier</th>
                    <th className="px-5 py-2">Slot</th>
                    <th className="px-5 py-2">Country</th>
                    <th className="px-5 py-2">Finding</th>
                    <th className="px-5 py-2 text-right">Transactions</th>
                    <th className="px-5 py-2">Note</th>
                    {canWrite && <th className="px-5 py-2"></th>}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => {
                    const copy = RECEIPT_STATUS_COPY[row.status];
                    return (
                      <tr key={row.id} className="border-t border-slate-100">
                        <td className="px-5 py-2 text-xs text-slate-600">
                          {entityLabel(entities, row.entity_id)}
                        </td>
                        <td className="px-5 py-2 font-medium text-slate-700">{row.supplier}</td>
                        <td className="px-5 py-2 font-mono text-xs">{row.slot}</td>
                        <td className="px-5 py-2 font-mono text-xs">{row.country || "—"}</td>
                        <td className="px-5 py-2">
                          <Badge
                            tone={
                              row.status === "missing"
                                ? "warning"
                                : row.status === "received_doc"
                                  ? "success"
                                  : "neutral"
                            }
                          >
                            {copy?.label ?? row.status}
                          </Badge>
                          {row.waived && (
                            <span className="ml-2 inline-block">
                              <Badge tone="neutral">Muted</Badge>
                            </span>
                          )}
                          {copy && (
                            <span className="block text-xs text-slate-400">{copy.meaning}</span>
                          )}
                        </td>
                        <td className="px-5 py-2 text-right tabular-nums">{row.txn_count}</td>
                        <td className="px-5 py-2 text-xs text-slate-500">{row.note ?? "—"}</td>
                        {canWrite && (
                          <td className="px-5 py-2 text-right">
                            <Button size="sm" variant="secondary" onClick={() => openEdit(row)}>
                              Mute or annotate
                            </Button>
                          </td>
                        )}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </QueryState>
      </Card>

      <Modal
        open={editing !== null}
        onClose={() => setEditing(null)}
        title="Mute or annotate this slot"
        description="A worklist note only. Muting takes the slot off the chase list; it changes no claim, no line, no lock and no figure."
        footer={
          <>
            <Button variant="secondary" onClick={() => setEditing(null)}>
              Cancel
            </Button>
            <Button
              loading={override.isPending}
              onClick={() =>
                editing &&
                override.mutate({
                  id: editing.id,
                  waived: form.waived,
                  note: form.note.trim() === "" ? null : form.note.trim(),
                })
              }
            >
              Save
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <label className="flex items-start gap-2 text-sm text-slate-700">
            <input
              type="checkbox"
              className="mt-1"
              checked={form.waived}
              onChange={(e) => setForm({ ...form, waived: e.target.checked })}
            />
            <span>
              Stop chasing this slot
              <span className="block text-xs text-slate-400">
                Survives the next close. It is not the claim-level waiver — that one lives on the
                claim and does affect what the claim contains.
              </span>
            </span>
          </label>
          <TextInput
            label="Note"
            hint="Why this slot is the way it is. Kept for whoever picks up the chase next."
            value={form.note}
            onChange={(e) => setForm({ ...form, note: e.target.value })}
            placeholder="Supplier confirmed no invoice for this half-month"
          />
        </div>
      </Modal>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// 3. Cadences (G3.5)
// --------------------------------------------------------------------------- //

function CadencesPanel({ canWrite, onRefusal, clearRefusal }: PanelProps) {
  const qc = useQueryClient();
  const [form, setForm] = useState({ supplier: "", cadence: CADENCES[1] as string });

  const cadences = useQuery<VatCadence[]>({
    queryKey: ["transport", "cadences"],
    queryFn: async () => (await api.get("/transport/cadences")).data,
  });

  const done = () => {
    clearRefusal();
    qc.invalidateQueries({ queryKey: ["transport", "cadences"] });
  };
  const save = useMutation({
    mutationFn: async () =>
      (
        await api.put(`/transport/cadences/${encodeURIComponent(form.supplier.trim())}`, {
          cadence: form.cadence,
        })
      ).data,
    onSuccess: () => {
      setForm({ supplier: "", cadence: CADENCES[1] as string });
      done();
    },
    onError: onRefusal,
  });
  const remove = useMutation({
    mutationFn: async (supplier: string) =>
      (await api.delete(`/transport/cadences/${encodeURIComponent(supplier)}`)).data,
    onSuccess: done,
    onError: onRefusal,
  });

  return (
    <div className="space-y-4">
      <Card title="How often each supplier invoices">
        <p className="text-xs text-slate-500">
          Receipt control expects an invoice per slot, and the cadence decides what a slot is. A
          supplier with no row here is not “no cadence”: the service falls back to the default it
          holds for that network.
        </p>
        {canWrite && (
          <div className="mt-4 grid gap-4 sm:grid-cols-3">
            <TextInput
              label="Supplier"
              required
              value={form.supplier}
              onChange={(e) => setForm({ ...form, supplier: e.target.value })}
              placeholder="Supplier code"
            />
            <Select
              label="Cadence"
              required
              value={form.cadence}
              onChange={(e) => setForm({ ...form, cadence: e.target.value })}
            >
              {CADENCES.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </Select>
            <div className="flex items-end">
              <Button
                loading={save.isPending}
                disabled={form.supplier.trim() === ""}
                onClick={() => save.mutate()}
              >
                Assign cadence
              </Button>
            </div>
          </div>
        )}
      </Card>

      <Card padded={false}>
        <QueryState
          query={cadences}
          loading={<Skeleton className="m-5 h-24 w-full" />}
          isEmpty={(rows) => rows.length === 0}
          empty={
            <EmptyState
              title="No cadence has been assigned by hand"
              description="Every supplier is following the default the service holds for its network."
            />
          }
          errorTitle="Couldn’t load the cadences"
        >
          {(rows) => (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <caption className="sr-only">Supplier invoicing cadences</caption>
                <thead>
                  <tr className="text-left text-xs text-slate-400">
                    <th className="px-5 py-2">Supplier</th>
                    <th className="px-5 py-2">Cadence</th>
                    {canWrite && <th className="px-5 py-2"></th>}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.id} className="border-t border-slate-100">
                      <td className="px-5 py-2 font-medium text-slate-700">{row.supplier}</td>
                      <td className="px-5 py-2 font-mono text-xs">{row.cadence}</td>
                      {canWrite && (
                        <td className="px-5 py-2 text-right">
                          <Button
                            size="sm"
                            variant="secondary"
                            loading={remove.isPending && remove.variables === row.supplier}
                            onClick={() => remove.mutate(row.supplier)}
                          >
                            Back to the default
                          </Button>
                        </td>
                      )}
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

// --------------------------------------------------------------------------- //
// 4. Note→invoice-ref overrides (R16/C4)
// --------------------------------------------------------------------------- //

function OverridesPanel({ canWrite, onRefusal, clearRefusal, entities }: PanelProps) {
  const qc = useQueryClient();
  const [form, setForm] = useState({
    entity_id: "",
    supplier: "",
    refund_country: "",
    invoice_ref: "",
    target_invoice_id: "",
  });

  const overrides = useQuery<VatNoteOverride[]>({
    queryKey: ["transport", "note-overrides"],
    queryFn: async () => (await api.get("/transport/note-overrides")).data,
  });

  // A CONVENIENCE only, on a different permission (INVOICE_READ): when the
  // caller can read the AP invoices we offer them as a pick-list, and when they
  // can't the target stays a typed id. No transport route enumerates invoices.
  const invoices = useQuery<InvoiceList>({
    queryKey: ["invoices", "override-targets"],
    queryFn: async () => (await api.get("/invoices", { params: { page_size: 100 } })).data,
    enabled: canWrite,
    retry: false,
  });

  const save = useMutation({
    mutationFn: async () =>
      (
        await api.put("/transport/note-overrides", {
          entity_id: form.entity_id,
          supplier: form.supplier.trim(),
          refund_country: form.refund_country.trim().toUpperCase(),
          invoice_ref: form.invoice_ref.trim(),
          target_invoice_id: form.target_invoice_id.trim(),
        })
      ).data,
    onSuccess: () => {
      clearRefusal();
      qc.invalidateQueries({ queryKey: ["transport", "note-overrides"] });
    },
    onError: onRefusal,
  });

  const ready =
    form.entity_id.trim() !== "" &&
    form.supplier.trim() !== "" &&
    form.refund_country.trim().length === 2 &&
    form.invoice_ref.trim() !== "" &&
    form.target_invoice_id.trim() !== "";

  return (
    <div className="space-y-4">
      <Card title="Point a statement reference at the right invoice">
        <p className="text-xs text-slate-500">
          When a fuel statement’s reference doesn’t match the supplier invoice it belongs to, this
          is where you say which invoice it means. It changes the association only — never an
          amount. Re-saving the same supplier, country and reference retargets it.
        </p>
        {canWrite && (
          <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <EntityPicker
              entities={entities}
              value={form.entity_id}
              onChange={(id) => setForm({ ...form, entity_id: id })}
              required
            />
            <TextInput
              label="Supplier"
              required
              value={form.supplier}
              onChange={(e) => setForm({ ...form, supplier: e.target.value })}
              placeholder="Supplier code"
            />
            <TextInput
              label="Refund country"
              hint="ISO code, e.g. LV"
              required
              maxLength={2}
              value={form.refund_country}
              onChange={(e) => setForm({ ...form, refund_country: e.target.value })}
              placeholder="LV"
            />
            <TextInput
              label="Statement reference"
              hint="The reference printed on the statement line."
              required
              value={form.invoice_ref}
              onChange={(e) => setForm({ ...form, invoice_ref: e.target.value })}
              placeholder="REF-…"
            />
            {invoices.data && invoices.data.items.length > 0 ? (
              <Select
                label="Supplier invoice it means"
                required
                value={form.target_invoice_id}
                onChange={(e) => setForm({ ...form, target_invoice_id: e.target.value })}
              >
                <option value="">Choose…</option>
                {invoices.data.items.map((inv) => (
                  <option key={inv.id} value={inv.id}>
                    {inv.invoice_number} · {inv.issue_date}
                  </option>
                ))}
              </Select>
            ) : (
              <TextInput
                label="Supplier invoice it means"
                hint="The registered invoice’s id."
                required
                value={form.target_invoice_id}
                onChange={(e) => setForm({ ...form, target_invoice_id: e.target.value })}
                placeholder="Invoice id"
              />
            )}
            <div className="flex items-end">
              <Button loading={save.isPending} disabled={!ready} onClick={() => save.mutate()}>
                Save mapping
              </Button>
            </div>
          </div>
        )}
      </Card>

      <Card padded={false}>
        <p className="px-5 pt-4 text-xs text-slate-400">
          A mapping stops applying on its own when its target invoice is de-registered, so there is
          no remove action here.
        </p>
        <QueryState
          query={overrides}
          loading={<Skeleton className="m-5 h-24 w-full" />}
          isEmpty={(rows) => rows.length === 0}
          empty={
            <EmptyState
              title="No reference mappings yet"
              description="Statement references are resolved automatically. Add a mapping only where that resolution gets it wrong."
            />
          }
          errorTitle="Couldn’t load the reference mappings"
        >
          {(rows) => (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <caption className="sr-only">Note to invoice reference mappings</caption>
                <thead>
                  <tr className="text-left text-xs text-slate-400">
                    <th className="px-5 py-2">Entity</th>
                    <th className="px-5 py-2">Supplier</th>
                    <th className="px-5 py-2">Country</th>
                    <th className="px-5 py-2">Statement reference</th>
                    <th className="px-5 py-2">Points at</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.id} className="border-t border-slate-100">
                      <td className="px-5 py-2 text-xs text-slate-600">
                        {entityLabel(entities, row.entity_id)}
                      </td>
                      <td className="px-5 py-2 font-medium text-slate-700">{row.supplier}</td>
                      <td className="px-5 py-2 font-mono text-xs">{row.refund_country}</td>
                      <td className="px-5 py-2 font-mono text-xs">{row.invoice_ref}</td>
                      <td className="px-5 py-2 font-mono text-xs text-slate-500">
                        {row.target_invoice_id}
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

// --------------------------------------------------------------------------- //
// 5. Engine tie-out expectations (R25 regime 2) — a REAL gate.
// --------------------------------------------------------------------------- //

const BLANK_TIEOUT = {
  entity_id: "",
  supplier: "",
  currency: "EUR",
  expected_lines: "",
  expected_gross_local: "",
  gross_local_tolerance: "0.02",
  expected_net_eur: "",
  expected_gross_eur: "",
  expected_diesel_litres: "",
};

/** Empty string ⇒ `null` (the field is genuinely not typed); anything else is
 * handed over EXACTLY as typed. The service is the only thing that quantizes a
 * figure (§4.9) — this never parses, rounds or re-formats one. */
function decimalOrNull(value: string): string | null {
  const trimmed = value.trim();
  return trimmed === "" ? null : trimmed;
}

function TieOutPanel({ canWrite, onRefusal, clearRefusal, entities }: PanelProps) {
  const qc = useQueryClient();
  const [period, setPeriod] = useState(currentPeriod());
  const [form, setForm] = useState({ ...BLANK_TIEOUT });

  const expectations = useQuery<VatTieOutExpectation[]>({
    queryKey: ["transport", "tie-out-expectations", period],
    queryFn: async () =>
      (await api.get("/transport/tie-out-expectations", { params: { period } })).data,
    enabled: isPeriodShape(period),
  });

  const done = () => {
    clearRefusal();
    qc.invalidateQueries({ queryKey: ["transport", "tie-out-expectations"] });
  };
  const save = useMutation({
    mutationFn: async () =>
      (
        await api.put("/transport/tie-out-expectations", {
          entity_id: form.entity_id,
          supplier: form.supplier.trim(),
          period,
          currency: form.currency.trim().toUpperCase(),
          // The ONE numeric field on this page: a line COUNT, not money. It is
          // sent as an integer because the schema types it `int`; every other
          // figure crosses as the string the operator typed.
          expected_lines: Number.parseInt(form.expected_lines.trim(), 10),
          expected_gross_local: decimalOrNull(form.expected_gross_local),
          gross_local_tolerance: form.gross_local_tolerance.trim(),
          expected_net_eur: decimalOrNull(form.expected_net_eur),
          expected_gross_eur: decimalOrNull(form.expected_gross_eur),
          expected_diesel_litres: decimalOrNull(form.expected_diesel_litres),
        })
      ).data,
    onSuccess: () => {
      setForm({ ...BLANK_TIEOUT });
      done();
    },
    onError: onRefusal,
  });
  const remove = useMutation({
    mutationFn: async (row: VatTieOutExpectation) =>
      (
        await api.delete("/transport/tie-out-expectations", {
          params: {
            entity_id: row.entity_id,
            supplier: row.supplier,
            period: row.period,
            currency: row.currency,
          },
        })
      ).data,
    onSuccess: done,
    onError: onRefusal,
  });

  const ready =
    form.entity_id.trim() !== "" &&
    form.supplier.trim() !== "" &&
    /^-?\d+$/.test(form.expected_lines.trim()) &&
    form.currency.trim().length === 3 &&
    form.gross_local_tolerance.trim() !== "" &&
    isPeriodShape(period);

  return (
    <div className="space-y-4">
      <Card title="What the supplier’s own invoice says">
        <p className="text-xs text-slate-500">
          Type the figures printed on the supplier’s invoice for the period.{" "}
          <strong>These stop the monthly close.</strong> Once a supplier has an expectation typed
          here, a close whose own figures disagree by more than the tolerance halts for that
          supplier instead of publishing. Leaving a supplier untyped leaves it unchecked — absence
          never halts anything.
        </p>
        <div className="mt-4 max-w-xs">
          <TextInput
            label="Period"
            hint="YYYY-MM — the accounting month on the invoice."
            value={period}
            onChange={(e) => setPeriod(e.target.value)}
            placeholder="2026-05"
          />
        </div>
        {canWrite && (
          <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <EntityPicker
              entities={entities}
              value={form.entity_id}
              onChange={(id) => setForm({ ...form, entity_id: id })}
              required
            />
            <TextInput
              label="Supplier"
              required
              value={form.supplier}
              onChange={(e) => setForm({ ...form, supplier: e.target.value })}
              placeholder="Supplier code"
            />
            <TextInput
              label="Currency"
              hint="As invoiced, e.g. EUR"
              required
              maxLength={3}
              value={form.currency}
              onChange={(e) => setForm({ ...form, currency: e.target.value })}
            />
            <TextInput
              label="Lines on the invoice"
              required
              inputMode="numeric"
              value={form.expected_lines}
              onChange={(e) => setForm({ ...form, expected_lines: e.target.value })}
              placeholder="0"
            />
            <TextInput
              label="Gross, as invoiced"
              hint="In the invoice currency."
              inputMode="decimal"
              value={form.expected_gross_local}
              onChange={(e) => setForm({ ...form, expected_gross_local: e.target.value })}
              placeholder="0.00"
            />
            <TextInput
              label="Tolerance"
              hint="Between 0.02 and 0.05."
              inputMode="decimal"
              value={form.gross_local_tolerance}
              onChange={(e) => setForm({ ...form, gross_local_tolerance: e.target.value })}
            />
            <TextInput
              label="Net, in EUR"
              inputMode="decimal"
              value={form.expected_net_eur}
              onChange={(e) => setForm({ ...form, expected_net_eur: e.target.value })}
              placeholder="0.00"
            />
            <TextInput
              label="Gross, in EUR"
              inputMode="decimal"
              value={form.expected_gross_eur}
              onChange={(e) => setForm({ ...form, expected_gross_eur: e.target.value })}
              placeholder="0.00"
            />
            <TextInput
              label="Diesel litres"
              inputMode="decimal"
              value={form.expected_diesel_litres}
              onChange={(e) => setForm({ ...form, expected_diesel_litres: e.target.value })}
              placeholder="0.000"
            />
            <div className="flex items-end">
              <Button loading={save.isPending} disabled={!ready} onClick={() => save.mutate()}>
                Save expectation
              </Button>
            </div>
          </div>
        )}
      </Card>

      <Card padded={false}>
        <QueryState
          query={expectations}
          loading={<Skeleton className="m-5 h-24 w-full" />}
          isEmpty={(rows) => rows.length === 0}
          empty={
            <EmptyState
              title="Nothing typed for this period"
              description="No supplier is being tied out for this period, so the close runs unchecked against an invoice."
            />
          }
          errorTitle="Couldn’t load the expectations"
        >
          {(rows) => (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <caption className="sr-only">Typed tie-out expectations for {period}</caption>
                <thead>
                  <tr className="text-left text-xs text-slate-400">
                    <th className="px-5 py-2">Entity</th>
                    <th className="px-5 py-2">Supplier</th>
                    <th className="px-5 py-2">Currency</th>
                    <th className="px-5 py-2 text-right">Lines</th>
                    <th className="px-5 py-2 text-right">Gross, as invoiced</th>
                    <th className="px-5 py-2 text-right">Tolerance</th>
                    <th className="px-5 py-2 text-right">Net EUR</th>
                    <th className="px-5 py-2 text-right">Gross EUR</th>
                    <th className="px-5 py-2 text-right">Diesel litres</th>
                    {canWrite && <th className="px-5 py-2"></th>}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.id} className="border-t border-slate-100">
                      <td className="px-5 py-2 text-xs text-slate-600">
                        {entityLabel(entities, row.entity_id)}
                      </td>
                      <td className="px-5 py-2 font-medium text-slate-700">{row.supplier}</td>
                      <td className="px-5 py-2 font-mono text-xs">{row.currency}</td>
                      <td className="px-5 py-2 text-right tabular-nums">{row.expected_lines}</td>
                      <td className="px-5 py-2 text-right tabular-nums">
                        {decimalMoney(row.expected_gross_local, row.currency)}
                      </td>
                      {/* A tolerance is a band, not an amount — shown as the exact
                          string it is, with no currency symbol attached. */}
                      <td className="px-5 py-2 text-right tabular-nums text-xs text-slate-500">
                        {row.gross_local_tolerance}
                      </td>
                      <td className="px-5 py-2 text-right tabular-nums">
                        {decimalMoney(row.expected_net_eur)}
                      </td>
                      <td className="px-5 py-2 text-right tabular-nums">
                        {decimalMoney(row.expected_gross_eur)}
                      </td>
                      <td className="px-5 py-2 text-right tabular-nums text-xs text-slate-500">
                        {row.expected_diesel_litres ?? "—"}
                      </td>
                      {canWrite && (
                        <td className="px-5 py-2 text-right">
                          <Button
                            size="sm"
                            variant="secondary"
                            loading={remove.isPending && remove.variables?.id === row.id}
                            onClick={() => remove.mutate(row)}
                          >
                            Stop checking
                          </Button>
                        </td>
                      )}
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

// --------------------------------------------------------------------------- //
// 6. The status-code vocabulary (R17) — reference only.
// --------------------------------------------------------------------------- //

function StatusCodesPanel() {
  const codes = useQuery<VatStatusCodes>({
    queryKey: ["transport", "status-codes"],
    queryFn: async () => (await api.get("/transport/status-codes")).data,
  });

  return (
    <Card title="Claim status codes">
      <QueryState
        query={codes}
        loading={<Skeleton className="h-20 w-full" />}
        errorTitle="Couldn’t load the status codes"
      >
        {(data) => (
          <div className="grid gap-6 sm:grid-cols-2">
            <div>
              <h3 className="text-sm font-semibold text-slate-700">Set by the service</h3>
              <p className="mt-1 text-xs text-slate-400">
                Derived from the submission checklist while a claim is still in preparation. These
                can’t be set by hand.
              </p>
              <ul className="mt-3 flex flex-wrap gap-2">
                {data.auto.map((code) => (
                  <li key={code}>
                    <Badge tone="neutral">{code}</Badge>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <h3 className="text-sm font-semibold text-slate-700">Set by you, after filing</h3>
              <p className="mt-1 text-xs text-slate-400">
                Where a filed claim stands with the refunding authority. Set one on the claim
                itself — it’s part of the filing workflow, not configuration.
              </p>
              <ul className="mt-3 flex flex-wrap gap-2">
                {data.manual.map((code) => (
                  <li key={code}>
                    <Badge tone="info">{code}</Badge>
                  </li>
                ))}
              </ul>
              <p className="mt-3 text-xs text-slate-400">
                <Link to="/vat-claims" className="underline">
                  Open a claim to set its code
                </Link>
              </p>
            </div>
          </div>
        )}
      </QueryState>
    </Card>
  );
}
