import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import {
  Button,
  Card,
  EmptyState,
  QueryState,
  Skeleton,
} from "../components/ui";
import { api, apiError } from "../lib/api";
import { shortDate } from "../lib/format";
import type {
  BulkAcknowledgeResult,
  CaptureFailure,
  CaptureFailureWorklist,
} from "../lib/types";

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
// Mirrors `bulk.MAX_BATCH` server-side. Duplicated deliberately rather than
// fetched: a settings round-trip just to render a button is worse than a number
// with a comment naming its source. If they drift the server still refuses
// (`bulk_too_many`) — this cap only decides whether the operator hits that
// refusal or is told before they act.
const MAX_BATCH = 200;

export default function CaptureFailures() {
  const qc = useQueryClient();
  const [showAcknowledged, setShowAcknowledged] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [openNote, setOpenNote] = useState<string | null>(null);
  const [note, setNote] = useState("");
  // L-4 multi-select. Keyed by ref_id, which is unique across both channels.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkNote, setBulkNote] = useState("");
  const [bulkResult, setBulkResult] = useState<BulkAcknowledgeResult | null>(
    null,
  );

  const key = ["captures", "failures", showAcknowledged] as const;
  const worklist = useQuery<CaptureFailureWorklist>({
    queryKey: key,
    queryFn: async () =>
      (
        await api.get(
          `/invoices/captures/failures?page_size=${MAX_BATCH}` +
            (showAcknowledged ? "&include_acknowledged=true" : ""),
        )
      ).data,
  });

  const refresh = () => {
    setErr(null);
    qc.invalidateQueries({ queryKey: ["captures"] });
  };

  const toggle = (refId: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(refId)) next.delete(refId);
      else next.add(refId);
      return next;
    });

  const bulkAcknowledge = useMutation({
    mutationFn: async (items: CaptureFailure[]) =>
      (
        await api.post("/invoices/captures/failures/acknowledge", {
          items: items.map((i) => ({ channel: i.channel, ref_id: i.ref_id })),
          // The count this screen DISPLAYED as selected when the button was
          // pressed. The server refuses the batch if it disagrees with what
          // arrived, so a selection built against a stale list cannot apply.
          agreed_count: items.length,
          selection: "explicit",
          note: bulkNote || null,
        })
      ).data as BulkAcknowledgeResult,
    onSuccess: (res) => {
      // Keep the per-record outcomes on screen. Collapsing a batch to "done"
      // throws away the half the operator most needs — which ones were skipped
      // and why.
      setBulkResult(res);
      setSelected(new Set());
      setBulkNote("");
      refresh();
    },
    onError: (e: unknown) => setErr(apiError(e)),
  });

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
          <h1 className="text-2xl font-semibold tracking-tight">
            Documents we could not read
          </h1>
          <p className="text-sm text-slate-500">
            Every document that arrived but never became an invoice — by upload
            or by email. Each one says what happened and what to do about it.
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
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => setShowAcknowledged((v) => !v)}
                >
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
                <h2 className="text-sm font-semibold text-slate-700">
                  Grouped by cause
                </h2>
                <ul className="mt-2 space-y-1 text-sm text-slate-600">
                  {data.groups.map((g) => (
                    <li
                      key={g.code}
                      className="flex items-baseline justify-between gap-4"
                    >
                      <span>{g.summary}</span>
                      <span className="shrink-0 tabular-nums text-slate-500">
                        {g.count} {g.count === 1 ? "document" : "documents"}
                      </span>
                    </li>
                  ))}
                </ul>
              </Card>
            )}

            {bulkResult && (
              <div className="rounded-md border border-slate-200 bg-slate-50 px-4 py-3 text-sm">
                <p className="font-medium text-slate-800">
                  Acknowledged {bulkResult.applied} of{" "}
                  {bulkResult.applied + bulkResult.skipped + bulkResult.failed}.
                </p>
                {/* Skips are reported plainly, NOT as errors: "already
                    acknowledged" is the system working, and colouring it red is
                    how operators learn to ignore red. */}
                {bulkResult.outcomes
                  .filter((o) => o.result !== "applied")
                  .map((o) => (
                    <p key={o.ref_id} className="mt-1 text-xs text-slate-600">
                      <span className="font-medium">
                        {o.result === "skipped" ? "Skipped" : "Failed"}:
                      </span>{" "}
                      {o.reason || "no reason given"}
                    </p>
                  ))}
                <button
                  type="button"
                  className="mt-2 text-xs font-medium text-brand-600 hover:underline"
                  onClick={() => setBulkResult(null)}
                >
                  Dismiss
                </button>
              </div>
            )}

            {selected.size > 0 && (
              <div className="flex flex-wrap items-center gap-3 rounded-md border border-brand-200 bg-brand-50 px-4 py-3 text-sm">
                <span className="font-medium text-slate-800">
                  {selected.size} selected
                  {selected.size >= MAX_BATCH && (
                    <span className="ml-1 font-normal text-slate-600">
                      (the most that can be acknowledged at once — do these,
                      then select again)
                    </span>
                  )}
                </span>
                <label className="sr-only" htmlFor="bulk-note">
                  What did you decide about these?
                </label>
                <input
                  id="bulk-note"
                  className="input min-w-[14rem] flex-1"
                  placeholder="What did you decide? (optional, applies to all)"
                  value={bulkNote}
                  onChange={(e) => setBulkNote(e.target.value)}
                />
                <Button
                  size="sm"
                  disabled={bulkAcknowledge.isPending}
                  onClick={() =>
                    bulkAcknowledge.mutate(
                      data.items.filter((i) => selected.has(i.ref_id)),
                    )
                  }
                >
                  Acknowledge {selected.size}
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => setSelected(new Set())}
                >
                  Clear
                </Button>
              </div>
            )}

            <div className="flex items-center justify-between gap-4 text-sm">
              <span className="text-slate-500">
                {/* `total` counts the whole filtered set, not this page, so this
                    sentence stays true when the list is longer than the page. */}
                {data.unacknowledged} unresolved of {data.total}
                {data.total > data.items.length
                  ? ` (showing the newest ${data.items.length})`
                  : ""}
                {data.items.some((i) => !i.acknowledged_at) && (
                  <>
                    {" · "}
                    <button
                      type="button"
                      className="font-medium text-brand-600 hover:underline"
                      onClick={() =>
                        setSelected(
                          new Set(
                            data.items
                              .filter((i) => !i.acknowledged_at)
                              .slice(0, MAX_BATCH)
                              .map((i) => i.ref_id),
                          ),
                        )
                      }
                    >
                      Select all unresolved
                      {data.items.filter((i) => !i.acknowledged_at).length >
                      MAX_BATCH
                        ? ` (first ${MAX_BATCH})`
                        : ""}
                    </button>
                  </>
                )}
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
                    <div className="flex min-w-0 items-start gap-3">
                      {!it.acknowledged_at && (
                        <>
                          <label
                            className="sr-only"
                            htmlFor={`sel-${it.ref_id}`}
                          >
                            Select {it.source_filename || "this document"}
                          </label>
                          <input
                            id={`sel-${it.ref_id}`}
                            type="checkbox"
                            className="mt-1 h-4 w-4 shrink-0"
                            checked={selected.has(it.ref_id)}
                            onChange={() => toggle(it.ref_id)}
                          />
                        </>
                      )}
                      <div className="min-w-0">
                        <p className="truncate font-medium text-slate-800">
                          {it.source_filename || "Untitled document"}
                        </p>
                        <p className="mt-0.5 text-xs text-slate-500">
                          {it.channel === "email"
                            ? "Emailed attachment"
                            : "Uploaded"}{" "}
                          · {shortDate(it.failed_at)}
                          {it.repeat_count > 1 && (
                            <>
                              {" · "}
                              <span className="text-amber-700">
                                this exact document has failed {it.repeat_count}{" "}
                                times — re-sending it will not help
                              </span>
                            </>
                          )}
                        </p>
                      </div>
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
                        <span className="badge bg-slate-100 text-slate-600">
                          Acknowledged
                        </span>
                      ) : (
                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={() => {
                            setOpenNote(
                              openNote === it.ref_id ? null : it.ref_id,
                            );
                            setNote("");
                          }}
                        >
                          Acknowledge
                        </Button>
                      )}
                    </div>
                  </div>

                  <p className="mt-3 text-sm font-medium text-slate-700">
                    {it.summary}
                  </p>
                  <p className="mt-1 text-sm text-slate-600">
                    {it.remediation}
                  </p>

                  <p className="mt-2 text-xs text-slate-500">
                    {it.document_retained
                      ? "Your original document is still stored — only reading it failed."
                      : "The stored copy of this document is gone; it has to be sent again."}
                  </p>

                  {it.acknowledged_at && (
                    <p className="mt-2 text-xs text-slate-500">
                      Acknowledged by {it.acknowledged_by || "someone"} on{" "}
                      {shortDate(it.acknowledged_at)}
                      {it.acknowledgement_note
                        ? ` — “${it.acknowledgement_note}”`
                        : ""}
                    </p>
                  )}

                  {it.detail && (
                    <details className="mt-2">
                      <summary className="cursor-pointer text-xs text-slate-400">
                        Technical detail (for support)
                      </summary>
                      <p className="mt-1 break-words text-xs text-slate-500">
                        {it.detail}
                      </p>
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
