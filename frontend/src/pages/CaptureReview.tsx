import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Button, ConfirmDialog, EmptyState, ErrorState, Skeleton, Spinner } from "../components/ui";
import { api, apiError } from "../lib/api";
import { METHOD_STYLES, methodLabel, money } from "../lib/format";
import type {
  DuplicateReport,
  ExtractionResult,
  FieldProvenance,
  InvoiceCreate,
  InvoiceDetail,
  Vendor,
} from "../lib/types";

/**
 * The extraction-review screen (E1.1): the source document side by side with what
 * was read from it — per field, with status (extracted/defaulted/missing),
 * confidence, original vs normalized vs reviewed values — plus duplicate warnings
 * and the confirm step that actually creates the invoice.
 *
 * §4.19: extraction output is ADVISORY. Nothing here writes to the invoice
 * record until the human confirms; corrections are recorded as provenance
 * (`reviewed_value`) through the audited review endpoint, never silently applied.
 */

const HEADER_FIELDS = [
  { key: "vendor_name", label: "Vendor", type: "text" },
  { key: "invoice_number", label: "Invoice number", type: "text" },
  { key: "issue_date", label: "Issue date", type: "date" },
  { key: "due_date", label: "Due date", type: "date" },
  { key: "currency", label: "Currency", type: "text" },
] as const;

type HeaderKey = (typeof HEADER_FIELDS)[number]["key"];

const STATUS_STYLES: Record<string, string> = {
  extracted: "bg-slate-100 text-slate-600",
  defaulted: "bg-amber-100 text-amber-700",
  missing: "bg-rose-100 text-rose-700",
};

const STATUS_HELP: Record<string, string> = {
  extracted: "read from the document",
  defaulted: "not on the document — filled with a default",
  missing: "not on the document",
};

function draftValue(draft: InvoiceCreate, key: HeaderKey): string {
  const v = draft[key];
  return v == null ? "" : String(v);
}

function ConfidenceNote({ f }: { f: FieldProvenance }) {
  if (f.status !== "extracted") return null;
  if (f.confidence == null) {
    return <span className="text-emerald-600">exact — structured source</span>;
  }
  const pct = Math.round(Number(f.confidence) * 100);
  if (f.low_confidence) {
    return (
      <span className="font-medium text-amber-600">low confidence ({pct}%) — please verify</span>
    );
  }
  return <span className="text-slate-500">{pct}% confidence</span>;
}

function SourcePane({ runId, filename }: { runId: string; filename: string | null }) {
  const src = useQuery({
    queryKey: ["captures", runId, "source"],
    staleTime: Infinity,
    gcTime: 0,
    retry: false,
    queryFn: async (): Promise<{ url: string; mime: string } | null> => {
      try {
        const res = await api.get(`/invoices/captures/${runId}/source`, { responseType: "blob" });
        const blob = res.data as Blob;
        const mime = String(res.headers["content-type"] || "application/octet-stream");
        return { url: URL.createObjectURL(new Blob([blob], { type: mime })), mime };
      } catch (e) {
        if (axios.isAxiosError(e) && e.response?.status === 404) return null;
        throw e;
      }
    },
  });

  // Revoke the object URL when it changes or the pane unmounts (gcTime 0 above
  // keeps the cache from handing back a revoked URL on remount).
  const url = src.data?.url;
  useEffect(() => {
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [url]);

  const name = filename || "document";
  return (
    <div className="card p-0">
      <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
        <h2 className="text-sm font-semibold text-slate-600">Original document</h2>
        {url && (
          <a href={url} download={name} className="text-sm font-medium text-brand-600 hover:underline">
            Download
          </a>
        )}
      </div>
      {src.isLoading && <Skeleton className="m-4 h-96 w-auto" />}
      {src.isError && (
        <ErrorState
          title="Couldn’t load the document"
          description="The extracted fields on the right are unaffected."
          onRetry={() => src.refetch()}
        />
      )}
      {src.data === null && (
        <EmptyState
          title="No stored document"
          description="This capture has no retrievable source file — review the fields against your own copy."
        />
      )}
      {src.data && src.data.mime.startsWith("application/pdf") && (
        <object
          data={src.data.url}
          type="application/pdf"
          className="h-[38rem] w-full"
          aria-label={`Original document ${name}`}
        >
          <EmptyState
            title="Preview unavailable"
            description="Your browser can’t display this PDF inline — use Download above."
          />
        </object>
      )}
      {src.data && src.data.mime.startsWith("image/") && (
        <img
          src={src.data.url}
          alt={`Original document ${name}`}
          className="max-h-[38rem] w-full object-contain p-2"
        />
      )}
      {src.data && !src.data.mime.startsWith("application/pdf") && !src.data.mime.startsWith("image/") && (
        <EmptyState
          title={name}
          description={`This ${src.data.mime} file has no inline preview — use Download above to open it.`}
        />
      )}
    </div>
  );
}

export default function CaptureReview() {
  const { runId = "" } = useParams<{ runId: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [draft, setDraft] = useState<InvoiceCreate | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [dupeAck, setDupeAck] = useState(false);

  // Poll the capture while the worker parses it (queued → running → parsed|failed).
  const poll = useQuery<ExtractionResult>({
    queryKey: ["captures", runId, "run"],
    queryFn: async () => (await api.get(`/invoices/upload/${runId}`)).data,
    refetchInterval: (q) => {
      const s = q.state.data?.status;
      return s === "queued" || s === "running" || s === undefined ? 1500 : false;
    },
  });
  const parsed = poll.data?.status === "parsed";

  // The LIVE provenance rows — including reviewed values recorded earlier, so a
  // reload never loses sight of a correction.
  const fields = useQuery<FieldProvenance[]>({
    queryKey: ["captures", runId, "fields"],
    queryFn: async () => (await api.get(`/invoices/captures/${runId}/fields`)).data,
    enabled: parsed,
  });

  // Seed the editable draft once from the parsed result, preferring any human
  // corrections already on file over the machine's values.
  useEffect(() => {
    if (draft || !parsed || !poll.data?.draft || !fields.data) return;
    const d: InvoiceCreate = { ...poll.data.draft.draft, extraction_run_id: runId };
    for (const f of fields.data) {
      if (f.reviewed_value != null && (HEADER_FIELDS as readonly { key: string }[]).some((h) => h.key === f.field)) {
        (d as unknown as Record<string, string | null>)[f.field] = f.reviewed_value || null;
      }
    }
    setDraft(d);
  }, [draft, parsed, poll.data, fields.data, runId]);

  // Duplicate candidates for the CURRENT (possibly corrected) invoice number.
  // The draft carries a vendor NAME; resolve it against the vendor master so the
  // backend can split exact (same supplier) from cross-supplier candidates.
  const vendors = useQuery<Vendor[]>({
    queryKey: ["vendors"],
    queryFn: async () => (await api.get("/vendors")).data,
    enabled: parsed,
  });
  const vendorName = draft?.vendor_name?.trim().toLowerCase() ?? "";
  const vendorId = vendorName
    ? vendors.data?.find((v) => v.name.trim().toLowerCase() === vendorName)?.id
    : undefined;
  const number = draft?.invoice_number?.trim() ?? "";
  const dupes = useQuery<DuplicateReport>({
    queryKey: ["captures", runId, "dupes", number, vendorId ?? null],
    queryFn: async () =>
      (
        await api.get("/invoices/duplicate-candidates", {
          params: { invoice_number: number, ...(vendorId ? { vendor_id: vendorId } : {}) },
        })
      ).data,
    enabled: parsed && number.length > 0,
  });

  const fieldRow = (key: HeaderKey) => fields.data?.find((f) => f.field === key);

  /** Header fields whose edited value differs from the captured record — these
   * are recorded as `reviewed_value` provenance through the audited endpoint. */
  const corrections = (): { field: string; reviewed_value: string }[] => {
    if (!draft || !fields.data) return [];
    const out: { field: string; reviewed_value: string }[] = [];
    for (const h of HEADER_FIELDS) {
      const row = fieldRow(h.key);
      if (!row) continue;
      const current = draftValue(draft, h.key);
      const captured = row.reviewed_value ?? row.value ?? "";
      if (current !== (captured || "")) out.push({ field: h.key, reviewed_value: current });
    }
    return out;
  };
  const pending = corrections();

  const saveCorrections = useMutation({
    mutationFn: async () =>
      (await api.post(`/invoices/captures/${runId}/review`, { fields: corrections() })).data,
    onSuccess: () => {
      setError(null);
      setNotice("Corrections recorded — the original capture is kept alongside them.");
      qc.invalidateQueries({ queryKey: ["captures", runId, "fields"] });
      qc.invalidateQueries({ queryKey: ["captures", "queue"] });
    },
    onError: (e) => setError(apiError(e)),
  });

  const confirm = useMutation({
    mutationFn: async () => {
      // Record any unsaved corrections first so the provenance trail matches
      // what is about to be saved, then create the invoice from the draft.
      const fix = corrections();
      if (fix.length > 0) {
        await api.post(`/invoices/captures/${runId}/review`, { fields: fix });
      }
      return (await api.post<InvoiceDetail>("/invoices", { ...draft, extraction_run_id: runId }))
        .data;
    },
    onSuccess: (inv) => {
      qc.invalidateQueries({ queryKey: ["captures", "queue"] });
      navigate(`/invoices/${inv.id}`);
    },
    onError: (e) => {
      setDupeAck(false);
      setError(apiError(e));
    },
  });

  const retry = useMutation({
    mutationFn: async () => (await api.post(`/invoices/upload/${runId}/retry`, {})).data,
    onSuccess: () => {
      setError(null);
      setDraft(null);
      qc.invalidateQueries({ queryKey: ["captures", runId] });
    },
    onError: (e) => setError(apiError(e)),
  });

  const canConfirm =
    !!draft && !!draft.vendor_name?.trim() && !!draft.invoice_number.trim() && !!draft.issue_date;

  const onConfirmClick = () => {
    if ((dupes.data?.exact.length ?? 0) > 0) setDupeAck(true);
    else confirm.mutate();
  };

  const heading = (
    <div className="flex items-end justify-between gap-4">
      <div>
        <nav aria-label="Breadcrumb" className="text-xs text-slate-400">
          <Link to="/captures" className="hover:text-brand-600 hover:underline">
            Captures
          </Link>{" "}
          / review
        </nav>
        <h1 className="text-2xl font-semibold tracking-tight">
          {poll.data?.draft?.draft.source_filename || "Capture review"}
        </h1>
        <p className="text-sm text-slate-500">
          Check what was read from the document before it becomes an invoice.
        </p>
      </div>
      {poll.data?.method && poll.data.method !== "unknown" && (
        <span className={`badge shrink-0 ${METHOD_STYLES[poll.data.method] ?? "bg-slate-100 text-slate-600"}`}>
          Read via {methodLabel(poll.data.method)}
        </span>
      )}
    </div>
  );

  if (poll.isError) {
    return (
      <div className="space-y-6">
        {heading}
        <div className="card p-0">
          <ErrorState title="Couldn’t load this capture" onRetry={() => poll.refetch()} />
        </div>
      </div>
    );
  }
  if (poll.isLoading || !poll.data) {
    return (
      <div className="space-y-6">
        {heading}
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  const run = poll.data;

  if (run.status === "queued" || run.status === "running") {
    return (
      <div className="space-y-6">
        {heading}
        <div className="card flex items-center gap-3 py-8" role="status" aria-live="polite">
          <Spinner />
          <div>
            <p className="text-sm font-medium text-slate-700">Reading the document…</p>
            <p className="text-sm text-slate-500">
              Parsing runs on the server — this page updates itself when the draft is ready.
            </p>
          </div>
        </div>
      </div>
    );
  }

  if (run.status === "failed") {
    return (
      <div className="space-y-6">
        {heading}
        {error && (
          <div role="alert" className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-700">
            {error}
          </div>
        )}
        <div className="card p-0">
          <ErrorState
            title="We couldn’t read this document"
            description={run.error || "The file could not be parsed."}
            onRetry={() => retry.mutate()}
            retryLabel={retry.isPending ? "Re-queuing…" : "Retry extraction"}
          />
        </div>
      </div>
    );
  }

  if (run.status === "saved") {
    return (
      <div className="space-y-6">
        {heading}
        <div className="card">
          <p className="text-sm text-slate-600">
            This capture has already been confirmed and saved as an invoice.
          </p>
          <Link to="/invoices" className="mt-2 inline-block text-sm font-medium text-brand-600 hover:underline">
            Go to invoices →
          </Link>
        </div>
      </div>
    );
  }

  const warnings = run.draft?.warnings ?? [];
  const exact = dupes.data?.exact ?? [];
  const crossSupplier = dupes.data?.cross_supplier ?? [];
  const subtotal = draft
    ? draft.line_items.reduce(
        (sum, li) => sum + Number(li.amount ?? Number(li.quantity) * Number(li.unit_price)),
        0,
      )
    : 0;

  return (
    <div className="space-y-6">
      {heading}

      {error && (
        <div role="alert" className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-700">
          {error}
        </div>
      )}
      {notice && !error && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-2 text-sm text-emerald-700">
          {notice}
        </div>
      )}
      {warnings.length > 0 && (
        <ul className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700">
          {warnings.map((w, i) => (
            <li key={i}>• {w}</li>
          ))}
        </ul>
      )}
      {(exact.length > 0 || crossSupplier.length > 0) && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          <p className="font-medium">
            {exact.length > 0
              ? `Possible duplicate: ${exact.length === 1 ? "an invoice" : `${exact.length} invoices`} with number ${number} already ${exact.length === 1 ? "exists" : "exist"} for this supplier.`
              : `Heads up: ${crossSupplier.length === 1 ? "another supplier has" : `${crossSupplier.length} other suppliers have`} an invoice numbered ${number}.`}
          </p>
          <ul className="mt-1 space-y-0.5">
            {[...exact, ...crossSupplier].slice(0, 5).map((c) => (
              <li key={c.invoice_id}>
                <Link to={`/invoices/${c.invoice_id}`} className="underline">
                  {c.vendor_name} · {c.invoice_number} · {money(c.total, c.currency)}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <SourcePane runId={runId} filename={run.draft?.draft.source_filename ?? null} />

        <div className="space-y-6">
          <div className="card space-y-4">
            <h2 className="text-sm font-semibold text-slate-600">Extracted fields</h2>
            {fields.isError && (
              <ErrorState
                title="Couldn’t load field provenance"
                onRetry={() => fields.refetch()}
              />
            )}
            {(fields.isLoading || !draft) && !fields.isError && <Skeleton className="h-64 w-full" />}
            {draft &&
              fields.data &&
              HEADER_FIELDS.map((h) => {
                const row = fieldRow(h.key);
                const changed = pending.some((p) => p.field === h.key);
                return (
                  <div key={h.key}>
                    <label className="label" htmlFor={`fld-${h.key}`}>
                      {h.label}
                    </label>
                    <input
                      id={`fld-${h.key}`}
                      className={`input ${row?.low_confidence ? "border-amber-400 bg-amber-50/50" : ""}`}
                      type={h.type}
                      value={draftValue(draft, h.key)}
                      aria-describedby={row ? `prov-${h.key}` : undefined}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          [h.key]: e.target.value === "" ? null : e.target.value,
                        } as InvoiceCreate)
                      }
                    />
                    {row && (
                      <p id={`prov-${h.key}`} className="mt-1 flex flex-wrap items-center gap-2 text-xs">
                        <span className={`badge ${STATUS_STYLES[row.status] ?? "bg-slate-100 text-slate-600"}`}>
                          {row.status}
                        </span>
                        <span className="text-slate-400">{STATUS_HELP[row.status] ?? ""}</span>
                        <ConfidenceNote f={row} />
                        {row.provider && <span className="text-slate-400">via {row.provider}</span>}
                        {row.original_value != null &&
                          row.normalized_value != null &&
                          row.original_value !== row.normalized_value && (
                            <span className="text-slate-500">
                              read “{row.original_value}” → normalized “{row.normalized_value}”
                            </span>
                          )}
                        {row.reviewed_value != null && (
                          <span className="text-emerald-600">
                            corrected to “{row.reviewed_value}” (original kept)
                          </span>
                        )}
                        {changed && <span className="font-medium text-brand-600">edited — not yet recorded</span>}
                      </p>
                    )}
                  </div>
                );
              })}
            {draft && fields.data && (
              <div className="flex items-center justify-between border-t border-slate-100 pt-3">
                <p className="text-xs text-slate-400">
                  Corrections are recorded next to the machine’s values — the capture itself is never
                  rewritten.
                </p>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={pending.length === 0 || saveCorrections.isPending}
                  loading={saveCorrections.isPending}
                  onClick={() => saveCorrections.mutate()}
                >
                  Save corrections
                </Button>
              </div>
            )}
          </div>

          {draft && (
            <div className="card space-y-3">
              <h2 className="text-sm font-semibold text-slate-600">Line items</h2>
              <div className="overflow-x-auto rounded-lg border border-slate-200">
                <table className="w-full text-sm">
                  <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
                    <tr>
                      <th scope="col" className="px-3 py-2">Description</th>
                      <th scope="col" className="px-3 py-2">Category</th>
                      <th scope="col" className="px-3 py-2 text-right">Qty</th>
                      <th scope="col" className="px-3 py-2 text-right">Unit</th>
                      <th scope="col" className="px-3 py-2 text-right">Amount</th>
                    </tr>
                  </thead>
                  <tbody>
                    {draft.line_items.map((li, i) => (
                      <tr key={i} className="border-t border-slate-100">
                        <td className="px-3 py-2">{li.description}</td>
                        <td className="px-3 py-2 text-slate-500">{li.category}</td>
                        <td className="px-3 py-2 text-right text-slate-500">{Number(li.quantity)}</td>
                        <td className="px-3 py-2 text-right text-slate-500">{money(li.unit_price)}</td>
                        <td className="px-3 py-2 text-right font-medium">
                          {money(li.amount ?? Number(li.quantity) * Number(li.unit_price))}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="text-xs text-slate-400">
                Line items don’t carry per-field confidence yet — check the amounts against the
                document. Totals are recomputed on the server when you confirm.
              </p>
              <div className="flex items-center justify-between">
                <span className="text-sm text-slate-500">Net of tax · subtotal {money(subtotal)}</span>
                <Button
                  disabled={!canConfirm || confirm.isPending}
                  loading={confirm.isPending}
                  onClick={onConfirmClick}
                >
                  Confirm &amp; create invoice
                </Button>
              </div>
              {!canConfirm && (
                <p className="text-xs text-amber-600">
                  Set a vendor, an invoice number and an issue date before confirming.
                </p>
              )}
            </div>
          )}
        </div>
      </div>

      <ConfirmDialog
        open={dupeAck}
        onClose={() => setDupeAck(false)}
        onConfirm={() => confirm.mutate()}
        title="Possible duplicate invoice"
        confirmLabel="Create anyway"
        tone="danger"
        loading={confirm.isPending}
      >
        <p className="text-sm text-slate-600">
          {exact.length === 1 ? "An invoice" : `${exact.length} invoices`} with number{" "}
          <span className="font-medium">{number}</span> from this supplier already{" "}
          {exact.length === 1 ? "exists" : "exist"}. Duplicate detection is advisory — you can still
          create this invoice if it is genuinely a different document.
        </p>
      </ConfirmDialog>
    </div>
  );
}
