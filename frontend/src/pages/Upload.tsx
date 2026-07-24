import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, apiError } from "../lib/api";
import { METHOD_STYLES, methodLabel, money } from "../lib/format";
import type {
  ExtractionResult,
  InvoiceCreate,
  InvoiceDetail,
  ParsedDraft,
  UploadAccepted,
} from "../lib/types";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export default function Upload() {
  const navigate = useNavigate();
  const [draft, setDraft] = useState<InvoiceCreate | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [method, setMethod] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // The parse/OCR now runs on the worker tier: upload returns 202 + a run id,
  // then we poll until the draft is ready (or the parse fails).
  const parse = useMutation({
    mutationFn: async (file: File): Promise<ParsedDraft> => {
      const form = new FormData();
      form.append("file", file);
      const accepted = (await api.post<UploadAccepted>("/invoices/upload", form)).data;
      const runId = accepted.extraction_run_id;
      // Poll for the result — backoff a little, cap the wait.
      for (let attempt = 0; attempt < 60; attempt++) {
        await sleep(attempt < 5 ? 500 : 1500);
        const res = (await api.get<ExtractionResult>(`/invoices/upload/${runId}`)).data;
        if (res.status === "parsed" && res.draft) return res.draft;
        if (res.status === "failed") throw new Error(res.error || "Could not read the document");
      }
      throw new Error("Still processing — check back shortly.");
    },
    onSuccess: (data) => {
      setDraft(data.draft);
      setWarnings(data.warnings);
      setMethod(data.method ?? null);
      setError(null);
    },
    onError: (e) => setError(apiError(e)),
  });

  const save = useMutation({
    mutationFn: async (payload: InvoiceCreate) =>
      (await api.post<InvoiceDetail>("/invoices", payload)).data,
    onSuccess: (inv) => navigate(`/invoices/${inv.id}`),
    onError: (e) => setError(apiError(e)),
  });

  const total = draft
    ? draft.line_items.reduce(
        (sum, li) => sum + Number(li.amount ?? Number(li.quantity) * Number(li.unit_price)),
        0,
      )
    : 0;

  const canSave = draft && draft.vendor_name?.trim() && draft.invoice_number.trim();

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Upload an invoice</h1>
        <p className="text-sm text-slate-500">
          Drop a <code className="rounded bg-slate-100 px-1">.pdf</code>,{" "}
          <code className="rounded bg-slate-100 px-1">.xml</code>,{" "}
          <code className="rounded bg-slate-100 px-1">.csv</code>, or{" "}
          <code className="rounded bg-slate-100 px-1">.json</code> file. We parse it into a draft you confirm.
          <span className="text-slate-400"> E-invoice XML (UBL/Factur-X) is read exactly; scanned PDFs use OCR.</span>
        </p>
      </div>

      <label className="card flex cursor-pointer flex-col items-center justify-center border-2 border-dashed border-slate-300 py-10 text-center hover:border-brand-400">
        <input
          type="file"
          accept=".pdf,.xml,.csv,.json"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) parse.mutate(f);
          }}
        />
        <div className="text-slate-500">
          {parse.isPending ? "Processing… (reading on the server)" : "Click to choose a file"}
        </div>
        <div className="mt-1 text-xs text-slate-400">PDF (text/scanned/Factur-X), e-invoice XML (UBL/CII), CSV, or JSON</div>
      </label>

      {error && <div className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-600">{error}</div>}

      {warnings.length > 0 && (
        <ul className="rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-700">
          {warnings.map((w, i) => (
            <li key={i}>• {w}</li>
          ))}
        </ul>
      )}

      {draft && (
        <div className="card space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-slate-600">Review draft</h2>
            {method && method !== "unknown" && (
              <span className={`badge ${METHOD_STYLES[method] ?? "bg-slate-100 text-slate-600"}`}>
                Read via {methodLabel(method)}
              </span>
            )}
          </div>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <div>
              <label className="label">Vendor</label>
              <input
                className="input"
                value={draft.vendor_name ?? ""}
                onChange={(e) => setDraft({ ...draft, vendor_name: e.target.value })}
              />
            </div>
            <div>
              <label className="label">Invoice number</label>
              <input
                className="input"
                value={draft.invoice_number}
                onChange={(e) => setDraft({ ...draft, invoice_number: e.target.value })}
              />
            </div>
            <div>
              <label className="label">Issue date</label>
              <input
                className="input"
                type="date"
                value={draft.issue_date}
                onChange={(e) => setDraft({ ...draft, issue_date: e.target.value })}
              />
            </div>
          </div>

          <div className="overflow-hidden rounded-lg border border-slate-200">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
                <tr>
                  <th className="px-3 py-2">Description</th>
                  <th className="px-3 py-2">Category</th>
                  <th className="px-3 py-2 text-right">Qty</th>
                  <th className="px-3 py-2 text-right">Unit</th>
                  <th className="px-3 py-2 text-right">Amount</th>
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

          <div className="flex items-center justify-between">
            <span className="text-sm text-slate-500">
              Net of tax · subtotal {money(total)}
            </span>
            <button
              className="btn-primary"
              disabled={!canSave || save.isPending}
              onClick={() => draft && save.mutate(draft)}
            >
              {save.isPending ? "Saving…" : "Save invoice"}
            </button>
          </div>
          {!canSave && (
            <p className="text-xs text-amber-600">Set a vendor and invoice number before saving.</p>
          )}
        </div>
      )}
    </div>
  );
}
