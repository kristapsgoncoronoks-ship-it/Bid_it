import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, ConfirmDialog } from "./ui";
import { toast } from "./Toast";
import { api, apiError } from "../lib/api";
import type { DeletionWarning } from "../lib/types";

// Mirrors `invoices.BIN_RETENTION_DAYS`. The Trash screen reads the real value
// off its own response; this path has no response body to read it from, so the
// number is duplicated here with its source named rather than left unsaid.
const BIN_RETENTION_DAYS = 30;

/**
 * Deleting one invoice, with the consent gate
 * (docs/design/deletion-and-archive.md, step 3).
 *
 * The flow is deliberately server-first. Clicking Delete asks the API; if the
 * invoice is consequential the API answers 409 `deletion_consent_required` AND
 * hands back the warning to display. Only then is a dialog shown, and confirming
 * re-submits the version the server just gave.
 *
 * That round trip is the point, not an inefficiency:
 *
 *  - the words on screen are always the server's current ones, so they cannot
 *    drift from what the audit trail records the client accepted;
 *  - the gate is the API's, not the dialog's — a caller that is not this SPA
 *    gets exactly the same protection;
 *  - a clean draft never sees a dialog at all. A warning shown for everything is
 *    a warning read for nothing, and this one has to be read.
 *
 * There is deliberately no "don't show this again" (owner decision): that is
 * consent given once for decisions not yet made.
 */
export function DeleteInvoiceButton({ invoiceId }: { invoiceId: string }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [warning, setWarning] = useState<DeletionWarning | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const remove = useMutation({
    mutationFn: async (acknowledgedVersion: string | null) => {
      // Axios sends a DELETE body under `data`.
      await api.delete(`/invoices/${invoiceId}`, {
        data: acknowledgedVersion
          ? { acknowledged_warning_version: acknowledgedVersion }
          : undefined,
      });
    },
    onSuccess: () => {
      // The moment the operator's belief that this is recoverable is either
      // formed or lost. Deleting used to navigate away in silence: the page you
      // were reading vanished and nothing said where the record went, on the
      // one path in the whole feature where somebody might panic. The bulk path
      // always said it; the single one — higher stakes — did not.
      toast.success(
        `Moved to Deleted invoices. Restorable for ${BIN_RETENTION_DAYS} days.`,
      );
      qc.invalidateQueries({ queryKey: ["invoices"] });
      navigate("/invoices");
    },
    onError: (e: unknown) => {
      const body = (e as { response?: { data?: Record<string, unknown> } })?.response?.data;
      if (body?.code === "deletion_consent_required" && body.warning) {
        // Not an error to report — it is the server asking a question.
        setErr(null);
        setWarning(body.warning as DeletionWarning);
        return;
      }
      setWarning(null);
      setErr(apiError(e));
    },
  });

  return (
    <>
      <Button
        variant="secondary"
        size="sm"
        disabled={remove.isPending}
        onClick={() => remove.mutate(null)}
      >
        Delete
      </Button>

      {err && <p className="mt-2 text-sm text-rose-700">{err}</p>}

      <ConfirmDialog
        open={warning !== null}
        onClose={() => setWarning(null)}
        onConfirm={() => {
          const version = warning?.version;
          setWarning(null);
          if (version) remove.mutate(version);
        }}
        title="Delete this invoice?"
        tone="danger"
        confirmLabel="I understand — delete it"
        loading={remove.isPending}
      >
        {warning && (
          <div className="space-y-3 text-sm text-slate-700">
            {/* Consequences FIRST. The general paragraph is 70 words of
                necessary hedging, and putting it above trains the eye to skim
                past the specific facts that would actually stop a bad decision.
                Both are rendered from the response, never composed here. */}
            {warning.consequences.length > 0 && (
              <>
                <p className="font-medium text-slate-800">Why this one matters:</p>
                <ul className="list-disc space-y-1 pl-5">
                  {warning.consequences.map((c) => (
                    <li key={c.code}>{c.message}</li>
                  ))}
                </ul>
              </>
            )}
            <p>{warning.text}</p>
          </div>
        )}
      </ConfirmDialog>
    </>
  );
}
