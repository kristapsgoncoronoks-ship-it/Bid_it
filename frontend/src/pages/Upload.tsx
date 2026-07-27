import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ConfirmDialog } from "../components/ui";
import { api, apiError, apiErrorCode } from "../lib/api";
import type { UploadAccepted } from "../lib/types";

interface UploadArgs {
  file: File;
  /** Explicit escape hatch for the E1.3 hash-based re-upload advisory. */
  override?: boolean;
}

/**
 * Pure upload surface. The parse/OCR runs on the worker tier: the POST returns
 * 202 + a capture-run id and we hand off to `/captures/{id}` — the extraction-
 * review screen (E1.1) — which polls the parse, shows per-field provenance and
 * owns the review → confirm step. One canonical review flow, not two.
 */
export default function Upload() {
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  // E1.3: a byte-identical prior upload is blocked (409, code=duplicate_upload)
  // unless explicitly overridden — this holds the file + the server's message
  // while the user decides.
  const [duplicate, setDuplicate] = useState<{ file: File; message: string } | null>(null);

  const upload = useMutation({
    mutationFn: async ({ file, override }: UploadArgs): Promise<UploadAccepted> => {
      const form = new FormData();
      form.append("file", file);
      const path = override ? "/invoices/upload?override=true" : "/invoices/upload";
      return (await api.post<UploadAccepted>(path, form)).data;
    },
    onSuccess: (accepted) => {
      setDuplicate(null);
      navigate(`/captures/${accepted.extraction_run_id}`);
    },
    onError: (e, vars) => {
      if (apiErrorCode(e) === "duplicate_upload") {
        setDuplicate({ file: vars.file, message: apiError(e) });
      } else {
        setError(apiError(e));
      }
    },
  });

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Upload an invoice</h1>
        <p className="text-sm text-slate-500">
          Drop a <code className="rounded-sm bg-slate-100 px-1">.pdf</code>,{" "}
          <code className="rounded-sm bg-slate-100 px-1">.xml</code>,{" "}
          <code className="rounded-sm bg-slate-100 px-1">.csv</code>, or{" "}
          <code className="rounded-sm bg-slate-100 px-1">.json</code> file. We parse it into a draft
          you review field by field — with confidence — before anything is saved.
          <span className="text-slate-400">
            {" "}
            E-invoice XML (UBL/Factur-X) is read exactly; scanned PDFs use OCR.
          </span>
        </p>
      </div>

      <label className="card flex cursor-pointer flex-col items-center justify-center border-2 border-dashed border-slate-300 py-10 text-center hover:border-brand-400">
        <input
          type="file"
          accept=".pdf,.xml,.csv,.json"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) upload.mutate({ file: f });
            e.target.value = ""; // allow re-selecting the same file
          }}
        />
        <div className="text-slate-500">
          {upload.isPending ? "Uploading…" : "Click to choose a file"}
        </div>
        <div className="mt-1 text-xs text-slate-400">
          PDF (text/scanned/Factur-X), e-invoice XML (UBL/CII), CSV, or JSON
        </div>
      </label>

      {error && (
        <div role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-600">
          {error}
        </div>
      )}

      <p className="text-sm text-slate-500">
        Earlier uploads that still need a look are in{" "}
        <Link to="/captures" className="font-medium text-brand-600 hover:underline">
          Captures
        </Link>
        .
      </p>

      <ConfirmDialog
        open={!!duplicate}
        onClose={() => setDuplicate(null)}
        onConfirm={() => {
          const d = duplicate!;
          upload.mutate({ file: d.file, override: true });
        }}
        title="You already uploaded this file"
        confirmLabel="Upload anyway"
        tone="danger"
        loading={upload.isPending}
      >
        {duplicate && <p className="text-sm text-slate-600">{duplicate.message}</p>}
      </ConfirmDialog>
    </div>
  );
}
