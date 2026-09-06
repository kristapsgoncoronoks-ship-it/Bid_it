import { useMutation, useQuery } from "@tanstack/react-query";
import axios from "axios";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { FileUpload } from "../components/ui";
import { api, apiError } from "../lib/api";
import type { BatchUploadAccepted, BatchUploadOutcome, Usage } from "../lib/types";

/**
 * PROD-004 (audit 2026-09-05): the monthly invoice cap used to announce itself
 * only AFTER the refusal, and the refusal said "ask a platform operator" — a
 * person the customer cannot reach. The upgrade path is the workspace's own
 * Plan & billing page; the person who can act on it is the owner. Warn before
 * the cap (last 10 %, at least 3) and point at the right door either way.
 */
function capWarningThreshold(limit: number): number {
  return Math.max(3, Math.ceil(limit * 0.1));
}

function UpgradePath({ canManageBilling }: { canManageBilling: boolean }) {
  return canManageBilling ? (
    <Link to="/billing" className="font-medium underline">
      Upgrade the plan
    </Link>
  ) : (
    <span>Ask your workspace owner to upgrade the plan.</span>
  );
}

function PlanUsageNotice({ capHit }: { capHit: boolean }) {
  const { hasPerm } = useAuth();
  const usage = useQuery<Usage>({
    queryKey: ["access", "usage"],
    queryFn: async () => (await api.get("/access/usage")).data,
  });
  const u = usage.data;
  const remaining = typeof u?.invoices_remaining === "number" ? u.invoices_remaining : null;
  const nearCap = remaining !== null && u && remaining <= capWarningThreshold(u.invoice_limit);
  if (usage.isError && !capHit) {
    // Advisory only — a failed usage read must not block an upload, but it
    // must not pass for "plenty left" either.
    return <p className="text-xs text-slate-400">Couldn’t check this month’s plan usage.</p>;
  }
  if (!capHit && !nearCap) return null;
  const reached = capHit || remaining === 0;
  return (
    <div
      role="status"
      className={
        reached
          ? "rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700"
          : "rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800"
      }
    >
      {u && remaining !== null ? (
        <>
          {reached ? "Monthly invoice limit reached: " : "Close to the monthly invoice limit: "}
          {u.invoices_used} of {u.invoice_limit} used, {remaining} remaining this month.{" "}
        </>
      ) : (
        <>This month's invoice limit for the plan is reached. </>
      )}
      <UpgradePath canManageBilling={hasPerm("billing.manage")} />
    </div>
  );
}

interface UploadArgs {
  files: File[];
  /** Explicit escape hatch for the E1.3 hash-based re-upload advisory. */
  override?: boolean;
}

/**
 * Pure upload surface. The parse/OCR runs on the worker tier: the POST returns
 * 202 + one capture-run id per file and we hand off to `/captures/{id}` — the
 * extraction-review screen (E1.1) — which polls the parse, shows per-field
 * provenance and owns the review → confirm step. One canonical review flow, not
 * two.
 *
 * WO-X: AP arrives in batches — an envelope, a supplier's monthly run, a folder
 * someone scanned — so this takes SEVERAL files and reports each one on its own
 * line. A batch is partial by design: one duplicate among nine good invoices
 * leaves nine queued and explains the tenth. Dropping a single file still walks
 * straight into its review screen, because that is the one-file flow and it did
 * not need a results list.
 */
export default function Upload() {
  const navigate = useNavigate();
  const [files, setFiles] = useState<File[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [outcomes, setOutcomes] = useState<BatchUploadOutcome[] | null>(null);
  // The files the LAST request carried. `files` is cleared on success so the
  // dropzone is ready for the next batch, but a refused file still has to be
  // re-sendable from its own result row.
  const [sent, setSent] = useState<File[]>([]);
  // PROD-004: a 402 is the cap, not a fault — show the upgrade path, not just the text.
  const [capHit, setCapHit] = useState(false);

  const upload = useMutation({
    mutationFn: async ({ files: chosen, override }: UploadArgs): Promise<BatchUploadAccepted> => {
      const form = new FormData();
      for (const f of chosen) form.append("files", f);
      const path = override ? "/invoices/upload/batch?override=true" : "/invoices/upload/batch";
      return (await api.post<BatchUploadAccepted>(path, form)).data;
    },
    onSuccess: (result) => {
      setError(null);
      setFiles([]);
      // One file in, one accepted: go where the person was already going.
      const first = result.outcomes[0];
      if (result.outcomes.length === 1 && first.accepted && first.extraction_run_id) {
        navigate(`/captures/${first.extraction_run_id}`);
        return;
      }
      setOutcomes(result.outcomes);
    },
    onError: (e) => {
      setError(apiError(e));
      setCapHit(axios.isAxiosError(e) && e.response?.status === 402);
    },
  });

  function send(override = false) {
    if (!files.length) return;
    setSent(files);
    upload.mutate({ files, override });
  }

  /** Re-send ONE refused file, waiving the re-upload advisory for it alone. */
  function uploadAnyway(name: string) {
    const again = sent.find((f) => f.name === name);
    if (again) upload.mutate({ files: [again], override: true });
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Upload invoices</h1>
        <p className="text-sm text-slate-500">
          Drop <code className="rounded-sm bg-slate-100 px-1">.pdf</code>,{" "}
          <code className="rounded-sm bg-slate-100 px-1">.xml</code>,{" "}
          <code className="rounded-sm bg-slate-100 px-1">.csv</code>, or{" "}
          <code className="rounded-sm bg-slate-100 px-1">.json</code> files — as many as arrived
          together. We parse each one into a draft you review field by field, with confidence,
          before anything is saved.
          <span className="text-slate-400">
            {" "}
            E-invoice XML (UBL/Factur-X) is read exactly; scanned PDFs use OCR.
          </span>
        </p>
      </div>

      <PlanUsageNotice capHit={capHit} />

      <FileUpload
        label="Invoices to capture"
        files={files}
        onFilesChange={(next) => {
          setFiles(next);
          setOutcomes(null);
        }}
        accept=".pdf,.xml,.csv,.json"
        multiple
        hint="PDF (text/scanned/Factur-X), e-invoice XML (UBL/CII), CSV, or JSON — up to 25 at a time"
        disabled={upload.isPending}
      />

      <button
        type="button"
        className="btn-primary"
        disabled={!files.length || upload.isPending}
        onClick={() => send()}
      >
        {upload.isPending
          ? "Uploading…"
          : files.length > 1
            ? `Upload ${files.length} files`
            : "Upload"}
      </button>

      {error && (
        <div role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-600">
          {error}
        </div>
      )}

      {outcomes && (
        <section className="card space-y-3" aria-label="Upload results">
          <h2 className="text-sm font-semibold text-slate-700">
            {outcomes.filter((o) => o.accepted).length} of {outcomes.length} queued for reading
          </h2>
          <ul className="divide-y divide-slate-100">
            {outcomes.map((o) => (
              <li key={o.filename} className="flex items-start justify-between gap-3 py-2 text-sm">
                <span className="min-w-0">
                  <span className="block truncate font-medium text-slate-700">{o.filename}</span>
                  {!o.accepted && <span className="block text-slate-500">{o.message}</span>}
                </span>
                {o.accepted && o.extraction_run_id ? (
                  <Link
                    to={`/captures/${o.extraction_run_id}`}
                    className="shrink-0 font-medium text-brand-600 hover:underline"
                  >
                    Review
                  </Link>
                ) : o.code === "duplicate_upload" ? (
                  <button
                    type="button"
                    className="shrink-0 font-medium text-brand-600 hover:underline"
                    onClick={() => uploadAnyway(o.filename)}
                  >
                    Upload anyway
                  </button>
                ) : (
                  <span role="alert" className="shrink-0 rounded-sm bg-rose-50 px-2 py-0.5 text-xs text-rose-600">
                    Not accepted
                  </span>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      <p className="text-sm text-slate-500">
        Earlier uploads that still need a look are in{" "}
        <Link to="/captures" className="font-medium text-brand-600 hover:underline">
          Captures
        </Link>
        .
      </p>
    </div>
  );
}
