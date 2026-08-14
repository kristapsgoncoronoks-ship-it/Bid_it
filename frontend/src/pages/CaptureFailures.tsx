import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Button, Card, EmptyState, QueryState, Skeleton } from "../components/ui";
import { api, apiError } from "../lib/api";
import { shortDate } from "../lib/format";
import type { CaptureFailure, CaptureFailureWorklist } from "../lib/types";

/**
 * The failed-capture worklist (H-1).
 *
 * The queue next door (`/captures`) shows what we READ. This one shows what we
 * could NOT read — the documents a customer believes were processed and were
 * not. Before this screen those were reachable only by polling a run id you had
 * to already know, so a silently failed capture had no surface anywhere.
 *
 * Three rules this page follows and should keep following:
 *
 *  - the explanation is the SERVER's (`summary` + `remediation`); this file
 *    never composes advice of its own, or the two would drift;
 *  - Retry is offered only when `retry_helps` — a button that provably cannot
 *    work teaches the operator to distrust every button;
 *  - "nothing here" is rendered as a POSITIVE statement, not an empty table.
 */
export default function CaptureFailures() {
  const qc = useQueryClient();
  const [showAcknowledged, setShowAcknowledged] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [openNote, setOpenNote] = useState<string | null>(null);
  const [note, setNote] = useState("");

  const key = ["captures", "failures", showAcknowledged] as const;
  const worklist = useQuery<CaptureFailureWorklist>({
    queryKey: key,
    queryFn: async () =>
      (
        await api.get(
          `/invoices/captures/failures${showAcknowledged ? "?include_acknowledged=true" : ""}`,
        )
      ).data,
  });

  const refresh = () => {
    setErr(null);
    qc.invalidateQueries({ queryKey: ["captures"] });
  };

  const acknowledge = useMutation({
    mutationFn: async (v: { item: CaptureFailure; note: string }) =>
      (
        await api.post(
          `/invoices/captures/failures/${v.item.channel}/${v.item.ref_id}/acknowledge`,
          { note: v.note || null },
        )
      ).data,
    onSuccess: () => {
      setOpenNote(null);
      setNote("");
      refresh();
    },
    onError: (e: unknown) => setErr(apiError(e)),
  });

  const retry = useMutation({
    mutationFn: async (runId: string) =>
      (await api.post(`/invoices/upload/${runId}/retry`)).data,
    onSuccess: refresh,
    onError: (e: unknown) => setErr(apiError(e)),
  });

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Documents we could not read</h1>
          <p className="text-sm text-slate-500">
            Every document that arrived but never became an invoice — by upload or by email. Each
            one says what happened and what to do about it.
          </p>
        </div>
        <Link to="/captures" className="btn-secondary shrink-0">
          Captures awaiting review
        </Link>
      </div>

      {err && (
        <div className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {err}
        </div>
      )}

      <QueryState
        query={worklist}
        loading={<Skeleton className="h-48 w-full" />}
        isEmpty={(d) => d.items.length === 0}
        empty={
          <div className="card p-0">
            <EmptyState
              title="Every document we received has been read"
              description="Nothing has failed capture. If a supplier says they sent something and it is not in your invoices, check the email intake address before assuming it was lost."
              action={
                <Button variant="secondary" size="sm" onClick={() => setShowAcknowledged((v) => !v)}>
                  {showAcknowledged ? "Hide resolved" : "Show acknowledged"}
                </Button>
              }
            />
          </div>
        }
        errorTitle="Couldn’t load the failure worklist"
      >
        {(data) => (
          <>
            {data.groups.length > 1 && (
              <Card className="p-4">
                <h2 className="text-sm font-semibold text-slate-700">Grouped by cause</h2>
                <ul className="mt-2 space-y-1 text-sm text-slate-600">
                  {data.groups.map((g) => (
                    <li key={g.code} className="flex items-baseline justify-between gap-4">
                      <span>{g.summary}</span>
                      <span className="shrink-0 tabular-nums text-slate-500">
                        {g.count} {g.count === 1 ? "document" : "documents"}
                      </span>
                    </li>
                  ))}
                </ul>
              </Card>
            )}

            <div className="flex items-center justify-between gap-4 text-sm">
              <span className="text-slate-500">
                {data.unacknowledged} unresolved of {data.total} shown
              </span>
              <button
                type="button"
                className="font-medium text-brand-600 hover:underline"
                onClick={() => setShowAcknowledged((v) => !v)}
              >
                {showAcknowledged ? "Hide acknowledged" : "Show acknowledged"}
              </button>
            </div>

            <div className="space-y-3">
              {data.items.map((it) => (
                <Card key={`${it.channel}:${it.ref_id}`} className="p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate font-medium text-slate-800">
                        {it.source_filename || "Untitled document"}
                      </p>
                      <p className="mt-0.5 text-xs text-slate-500">
                        {it.channel === "email" ? "Emailed attachment" : "Uploaded"} ·{" "}
                        {shortDate(it.failed_at)}
                        {it.repeat_count > 1 && (
                          <>
                            {" · "}
                            <span className="text-amber-700">
                              this exact document has failed {it.repeat_count} times — re-sending it
                              will not help
                            </span>
                          </>
                        )}
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      {it.retry_helps && it.channel === "upload" && (
                        <Button
                          size="sm"
                          variant="secondary"
                          disabled={retry.isPending}
                          onClick={() => retry.mutate(it.ref_id)}
                        >
                          Retry
                        </Button>
                      )}
                      {it.acknowledged_at ? (
                        <span className="badge bg-slate-100 text-slate-600">Acknowledged</span>
                      ) : (
                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={() => {
                            setOpenNote(openNote === it.ref_id ? null : it.ref_id);
                            setNote("");
                          }}
                        >
                          Acknowledge
                        </Button>
                      )}
                    </div>
                  </div>

                  <p className="mt-3 text-sm font-medium text-slate-700">{it.summary}</p>
                  <p className="mt-1 text-sm text-slate-600">{it.remediation}</p>

                  <p className="mt-2 text-xs text-slate-500">
                    {it.document_retained
                      ? "Your original document is still stored — only reading it failed."
                      : "The stored copy of this document is gone; it has to be sent again."}
                  </p>

                  {it.acknowledged_at && (
                    <p className="mt-2 text-xs text-slate-500">
                      Acknowledged by {it.acknowledged_by || "someone"} on{" "}
                      {shortDate(it.acknowledged_at)}
                      {it.acknowledgement_note ? ` — “${it.acknowledgement_note}”` : ""}
                    </p>
                  )}

                  {it.detail && (
                    <details className="mt-2">
                      <summary className="cursor-pointer text-xs text-slate-400">
                        Technical detail (for support)
                      </summary>
                      <p className="mt-1 break-words text-xs text-slate-500">{it.detail}</p>
                    </details>
                  )}

                  {openNote === it.ref_id && (
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      <label className="sr-only" htmlFor={`note-${it.ref_id}`}>
                        What did you decide about this document?
                      </label>
                      <input
                        id={`note-${it.ref_id}`}
                        className="input min-w-[16rem] flex-1"
                        placeholder="What did you decide? (optional)"
                        value={note}
                        onChange={(e) => setNote(e.target.value)}
                      />
                      <Button
                        size="sm"
                        disabled={acknowledge.isPending}
                        onClick={() => acknowledge.mutate({ item: it, note })}
                      >
                        Record it
                      </Button>
                    </div>
                  )}
                </Card>
              ))}
            </div>
          </>
        )}
      </QueryState>
    </div>
  );
}
