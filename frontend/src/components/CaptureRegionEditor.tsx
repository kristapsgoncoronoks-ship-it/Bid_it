import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import { useEffect, useRef, useState, type PointerEvent } from "react";
import { useAuth } from "../auth/AuthContext";
import { api, apiError, isCanceledRequest, tokenStore } from "../lib/api";
import { currentRegionValue, fieldKey, REGION_HEADERS, REGION_LINES, regionFromPoints, validRect, validateRegionRead,
  type RegionApply, type RegionHistory, type RegionRead, type RegionReadRequest, type RegionRect, type RegionRotation } from "../lib/captureRegions";
import type { FieldProvenance, InvoiceCreate } from "../lib/types";
import { Button, ErrorState } from "./ui";

export interface CaptureRegionContext {
  draft: InvoiceCreate; fields: FieldProvenance[]; disabled: boolean; dirty: boolean;
  onBusyChange: (busy: boolean) => void; onApplied: () => Promise<void>;
}
interface Props extends CaptureRegionContext { runId: string; page: number; previewUrl: string }
const FULL_PAGE: RegionRect = { x: 0, y: 0, width: 1, height: 1 };
const LABELS = Object.fromEntries([...REGION_HEADERS, ...REGION_LINES]);
const terminal = (read?: RegionRead) => read && !["queued", "running"].includes(read.status);

/** A reduced image is used only for selection. The worker reads the original. */
export function CaptureRegionEditor({ runId, page, previewUrl, draft, disabled, dirty, onBusyChange, onApplied }: Props) {
  const { cacheIdentity } = useAuth();
  const qc = useQueryClient();
  const scope = `${cacheIdentity}:${runId}:${page}:${tokenStore.generation()}`;
  const live = useRef(scope); live.current = scope;
  useEffect(() => { live.current = scope; return () => { live.current = ""; }; }, [scope]);
  const active = () => live.current === scope;
  const base = `/invoices/captures/${runId}/region-reads`;
  const [rotation, setRotation] = useState<RegionRotation>(0);
  const [zoom, setZoom] = useState(1);
  const [rect, setRect] = useState<RegionRect | null>(null);
  const [target, setTarget] = useState("header:invoice_number");
  const [canvasSize, setCanvasSize] = useState({ width: 1, height: 1 });
  const [imageError, setImageError] = useState(false);
  const canvas = useRef<HTMLCanvasElement>(null);
  const dragStart = useRef<{ x: number; y: number } | null>(null);
  const [readIntent, setReadIntent] = useState<RegionReadRequest | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [readUncertain, setReadUncertain] = useState(false);
  const [readMissing, setReadMissing] = useState(false);
  const [applyIntent, setApplyIntent] = useState<RegionApply | null>(null);
  const [applyUncertain, setApplyUncertain] = useState(false);
  const [knownApplied, setKnownApplied] = useState(false);
  const [applyMissing, setApplyMissing] = useState(false);
  const [comparisonStale, setComparisonStale] = useState(false);
  const [settingsStale, setSettingsStale] = useState(false);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [chosen, setChosen] = useState<Record<string, boolean>>({});
  const [values, setValues] = useState<Record<string, string>>({});
  const [rowFields, setRowFields] = useState<Record<number, string[]>>({});
  const [rowTargets, setRowTargets] = useState<Record<number, string>>({});
  const [overwriteReviewed, setOverwriteReviewed] = useState(false);
  const history = useQuery<RegionHistory>({ queryKey: ["capture-regions", cacheIdentity, runId], retry: false,
    queryFn: async ({ signal }) => {
      const data = (await api.get(base, { signal })).data;
      if (!Array.isArray(data?.items) || typeof data.eligible !== "boolean" || !Number.isInteger(data.attempt_seq)) throw new Error("Re-read settings are unavailable.");
      return data;
    } });
  const detail = useQuery<RegionRead>({ queryKey: ["capture-region", cacheIdentity, runId, selectedId], enabled: !!selectedId, retry: false,
    queryFn: async ({ signal }) => validateRegionRead((await api.get(`${base}/${selectedId}`, { signal })).data, runId),
    refetchInterval: (query) => ["queued", "running"].includes(query.state.data?.status ?? "") ? 1500 : false });
  const read = detail.data;
  const saved = read?.current_draft ?? history.data?.current_draft ?? draft;
  const reviewed = new Set((read?.reviewed_fields ?? history.data?.reviewed_fields ?? []).map((field) => fieldKey(field.field, field.line_index)));

  useEffect(() => {
    let current = true;
    const image = new Image();
    image.onload = () => {
      if (!current || !canvas.current) return;
      const sourceWidth = image.naturalWidth, sourceHeight = image.naturalHeight;
      const turned = rotation === 90 || rotation === 270;
      const width = turned ? sourceHeight : sourceWidth, height = turned ? sourceWidth : sourceHeight;
      canvas.current.width = width; canvas.current.height = height;
      const context = canvas.current.getContext("2d");
      if (!context) { setImageError(true); return; }
      context.translate(width / 2, height / 2); context.rotate(rotation * Math.PI / 180);
      context.drawImage(image, -sourceWidth / 2, -sourceHeight / 2);
      setCanvasSize({ width, height }); setImageError(false);
    };
    image.onerror = () => { if (current) setImageError(true); };
    image.src = previewUrl;
    return () => { current = false; };
  }, [previewUrl, rotation]);

  // A newly observed saved base requires a deliberate new choice of changes.
  useEffect(() => {
    setChosen({}); setValues({}); setRowFields({}); setRowTargets({}); setOverwriteReviewed(false);
  }, [read?.id, read?.review_fingerprint]);
  useEffect(() => {
    if (read && terminal(read) && active()) void history.refetch();
  }, [read?.id, read?.status]);

  async function finishApplied(row: RegionRead, expectedKey = applyIntent?.command_key) {
    if (!active()) return;
    if (!expectedKey || row.apply_command_key !== expectedKey || !row.applied_result) {
      setApplyUncertain(true); setError("Another application was recorded. Refresh the saved capture before continuing."); return;
    }
    setKnownApplied(true); setApplyMissing(false); setApplyUncertain(true);
    try {
      await onApplied();
      if (!active()) return;
      setError(null); setNotice("Selected changes applied and loaded from the saved capture. The original document and unselected corrections are kept.");
      setApplyUncertain(false); setKnownApplied(false); setApplyIntent(null);
      void history.refetch();
    } catch (e) {
      if (!active() || isCanceledRequest(e)) return;
      setError("The changes were applied, but the saved capture could not reload. Check the applied result; do not apply it again.");
    }
  }
  function acceptRead(row: RegionRead) {
    setSelectedId(row.id); setReadUncertain(false); setReadMissing(false);
    qc.setQueryData(["capture-region", cacheIdentity, runId, row.id], row);
  }
  const create = useMutation({ mutationFn: async (request: RegionReadRequest) => {
    if (!active()) throw new Error("This capture is no longer open.");
    return validateRegionRead((await api.post(base, request)).data, runId, request.command_key);
  }, onSuccess: (row) => { if (!active()) return; acceptRead(row); setError(null); setNotice("Re-read request accepted. Existing capture values have not changed."); void history.refetch(); },
    onError: (e) => {
      if (!active() || isCanceledRequest(e)) return;
      setError(apiError(e));
      const uncertain = !axios.isAxiosError(e) || !e.response || e.response.status >= 500;
      if (axios.isAxiosError(e) && e.response?.status === 409) setSettingsStale(true);
      setReadUncertain(uncertain); if (!uncertain) setReadIntent(null);
    } });
  const apply = useMutation({ mutationFn: async ({ id, request }: { id: string; request: RegionApply }) => {
    if (!active()) throw new Error("This capture is no longer open.");
    return validateRegionRead((await api.post(`${base}/${id}/apply`, request)).data, runId);
  }, onSuccess: async (row, { request }) => { if (!active()) return; qc.setQueryData(["capture-region", cacheIdentity, runId, row.id], row); await finishApplied(row, request.command_key); },
    onError: (e) => {
      if (!active() || isCanceledRequest(e)) return;
      setError(axios.isAxiosError(e) && e.response?.status === 409 ? "The saved capture changed. Refresh the comparison and choose the changes again." : apiError(e));
      const uncertain = !axios.isAxiosError(e) || !e.response || e.response.status >= 500;
      if (axios.isAxiosError(e) && e.response?.status === 409) setComparisonStale(true);
      setApplyMissing(false); setApplyUncertain(uncertain); if (!uncertain) setApplyIntent(null);
    } });
  const working = create.isPending || apply.isPending || checking || readUncertain || applyUncertain || (read?.status === "queued" || read?.status === "running");
  useEffect(() => { onBusyChange(working); return () => onBusyChange(false); }, [working, onBusyChange]);
  const locked = disabled || create.isPending || apply.isPending || checking || readUncertain || applyUncertain || read?.status === "queued" || read?.status === "running";

  async function checkRead() {
    if (!readIntent || !active()) return;
    setChecking(true);
    try {
      const row = validateRegionRead((await api.get(`${base}/by-command/${readIntent.command_key}`)).data, runId, readIntent.command_key);
      if (!active()) return; acceptRead(row); setError(null); setNotice("The stored re-read outcome was found. No new read was requested.");
    } catch (e) {
      if (!active() || isCanceledRequest(e)) return;
      setReadMissing(axios.isAxiosError(e) && e.response?.status === 404);
      setError(axios.isAxiosError(e) && e.response?.status === 404 ? "No committed read was found. You can retry the original request with the same command key." : apiError(e));
    } finally { if (active()) setChecking(false); }
  }
  async function checkApplied() {
    if (!selectedId || !applyIntent || !active()) return;
    setChecking(true);
    try {
      const row = validateRegionRead((await api.get(`${base}/${selectedId}`)).data, runId);
      if (!active()) return;
      qc.setQueryData(["capture-region", cacheIdentity, runId, row.id], row);
      if (row.applied) await finishApplied(row);
      else { setKnownApplied(false); setApplyMissing(true); setError("No application receipt is recorded yet. You can retry this exact selection using the original command key."); }
    } catch (e) { if (active() && !isCanceledRequest(e)) setError(apiError(e)); }
    finally { if (active()) setChecking(false); }
  }
  async function refreshComparison() {
    if (!active()) return;
    setChecking(true);
    const result = selectedId ? await detail.refetch() : await history.refetch();
    if (!active()) return;
    setChecking(false);
    if (result.isError) setError("Couldn’t refresh the saved comparison. Try again before applying changes.");
    else { setError(null); setChosen({}); setRowFields({}); setOverwriteReviewed(false); setComparisonStale(false); }
  }
  async function refreshSettings() {
    const result = await history.refetch();
    if (!active()) return;
    if (result.isError) setError("Couldn’t refresh re-read controls. Your current values are unchanged.");
    else { setSettingsStale(false); setError(null); }
  }
  function startRead() {
    if (!rect || !validRect(rect) || !history.data?.source_sha256 || !history.data.eligible || locked) return;
    const [kind, a, b] = target.split(":");
    const request: RegionReadRequest = { command_key: crypto.randomUUID(), expected_attempt_seq: history.data.attempt_seq, source_sha256: history.data.source_sha256,
      page, ...rect, rotation, language: "eng", target: kind === "table" ? "table" : "field",
      ...(kind === "header" ? { field: a } : kind === "line" ? { line_index: Number(a), field: b } : {}) };
    setReadIntent(request); setSelectedId(null); setApplyIntent(null); setKnownApplied(false); setReadMissing(false); setComparisonStale(false); setError(null); setNotice(null); create.mutate(request);
  }
  function pointerPoint(event: PointerEvent<SVGSVGElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    return { x: (event.clientX - bounds.left) / bounds.width, y: (event.clientY - bounds.top) / bounds.height };
  }
  function moveSelection(event: PointerEvent<SVGSVGElement>) {
    if (dragStart.current && !locked) setRect(regionFromPoints(dragStart.current, pointerPoint(event)));
  }
  const proposed = read?.result?.fields ?? [];
  const selectedFields = proposed.filter((field) => chosen[fieldKey(field.field, field.line_index)]).map((field) => ({ ...field, value: values[fieldKey(field.field, field.line_index)] ?? field.value }));
  const mappedRows = (read?.result?.rows ?? []).flatMap((_, index) => (rowFields[index]?.length ?? 0) > 0 ? [{ candidate_index: index,
    target_line_index: (rowTargets[index] ?? (index < saved.line_items.length ? String(index) : "append")) === "append" ? null : Number(rowTargets[index] ?? index), fields: rowFields[index] }] : []);
  const touchesReviewed = selectedFields.some((field) => reviewed.has(fieldKey(field.field, field.line_index))) || mappedRows.some((row) => row.target_line_index != null && row.fields.some((field) => reviewed.has(fieldKey(field, row.target_line_index))));
  const appendIncomplete = mappedRows.some((row) => row.target_line_index == null && ["description", "quantity", "unit_price", "amount"].some((field) => !row.fields.includes(field)));
  const canApply = read?.status === "ready" && !!history.data?.eligible && !history.isError && !detail.isError && !detail.isFetching && !locked && !dirty && !comparisonStale && (selectedFields.length > 0 || mappedRows.length > 0) && (!touchesReviewed || overwriteReviewed) && !appendIncomplete;
  function applySelected() {
    if (!read || !canApply) return;
    const request: RegionApply = { command_key: crypto.randomUUID(), expected_review_fingerprint: read.review_fingerprint,
      fields: selectedFields, rows: mappedRows, overwrite_reviewed: touchesReviewed && overwriteReviewed };
    setApplyIntent(request); setNotice(null); setError(null); apply.mutate({ id: read.id, request });
  }

  return <section aria-label="Visual OCR adjustment" className="space-y-4 border-t border-slate-200 p-3">
    <div><h3 className="font-semibold text-slate-800">Select → Re-read → Compare → Apply</h3>
      <p className="mt-1 text-xs text-slate-500">Draw over this preview or enter an area below. The reader uses the original-quality page. English is the supported reader language for this first version.</p></div>
    {error && <p role="alert" className="text-sm text-rose-700">{error}</p>}
    {notice && <p role="status" className="text-sm text-slate-600">{notice}</p>}
    {history.isPending && <p role="status">Loading re-read controls…</p>}
    {history.isError && <ErrorState title="Couldn’t load re-read controls" onRetry={() => void history.refetch()} />}
    <Button variant="ghost" size="sm" disabled={locked || history.isFetching} onClick={() => void refreshSettings()}>Refresh re-read controls</Button>
    {history.data && !history.data.eligible && <p role="alert" className="text-sm text-amber-700">{history.data.reason || "This capture is no longer available for re-reading."}</p>}
    {imageError ? <p role="alert">The selection image could not load. Close the editor and retry the page preview.</p> : <>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="secondary" size="sm" disabled={locked} onClick={() => { setRotation(((rotation + 90) % 360) as RegionRotation); setRect(null); }}>Rotate page clockwise</Button>
        <span className="text-xs text-slate-500">{rotation}° clockwise</span>
        <Button variant="ghost" size="sm" disabled={locked || zoom <= 0.5} onClick={() => setZoom((value) => Math.max(0.5, value - 0.25))}>Zoom out</Button>
        <span className="text-xs">{Math.round(zoom * 100)}%</span>
        <Button variant="ghost" size="sm" disabled={locked || zoom >= 2} onClick={() => setZoom((value) => Math.min(2, value + 0.25))}>Zoom in</Button>
      </div>
      <div className="overflow-auto rounded-lg border border-slate-200 bg-slate-100 p-2" style={{ maxHeight: "34rem" }}>
        <div className="relative mx-auto bg-white" style={{ width: `${zoom * 100}%`, minWidth: `${zoom * 240}px`, aspectRatio: `${canvasSize.width} / ${canvasSize.height}` }}>
          <canvas ref={canvas} aria-label={`Page ${page} selection preview`} className="block h-full w-full" />
          <svg aria-label="OCR selection area" role="img" className="absolute inset-0 h-full w-full touch-none" viewBox="0 0 1000 1000" preserveAspectRatio="none"
            onPointerDown={(event) => { if (locked) return; event.currentTarget.setPointerCapture(event.pointerId); dragStart.current = pointerPoint(event); setRect(null); }}
            onPointerMove={moveSelection} onPointerUp={(event) => { moveSelection(event); dragStart.current = null; if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }}
            onPointerCancel={() => { dragStart.current = null; }}>
            {rect && <rect x={rect.x * 1000} y={rect.y * 1000} width={rect.width * 1000} height={rect.height * 1000} fill="rgba(59,110,242,0.15)" stroke="#3b6ef2" strokeWidth="3" vectorEffect="non-scaling-stroke" />}
          </svg>
        </div>
      </div>
      <fieldset disabled={locked} className="space-y-2"><legend className="text-xs font-medium text-slate-600">Selection area · percentage of the rotated page</legend>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">{(["x", "y", "width", "height"] as const).map((key) => <label key={key} className="text-xs text-slate-600">
          {({ x: "Left", y: "Top", width: "Width", height: "Height" })[key]} (%)
          <input className="input mt-1 px-2 py-1 text-sm" type="number" min="0" max="100" step="0.1" aria-label={`Selection ${key === "x" ? "left" : key === "y" ? "top" : key} (%)`}
            value={rect ? Number((rect[key] * 100).toFixed(2)) : ""} onChange={(event) => setRect({ ...(rect ?? FULL_PAGE), [key]: Number(event.target.value) / 100 })} />
        </label>)}</div>
        <div className="flex flex-wrap gap-2"><Button variant="ghost" size="sm" disabled={locked} onClick={() => setRect({ ...FULL_PAGE })}>Select whole page</Button>
          <Button variant="ghost" size="sm" disabled={locked || !rect} onClick={() => setRect(null)}>Clear selection</Button></div>
        {rect && !validRect(rect) && <p className="text-xs text-amber-700">Choose a non-empty area fully inside the page.</p>}
        <label className="block text-xs font-medium text-slate-600">Read as
          <select className="input mt-1" aria-label="Read as" value={target} onChange={(event) => setTarget(event.target.value)}>
            <optgroup label="Invoice fields">{REGION_HEADERS.map(([key, label]) => <option key={key} value={`header:${key}`}>{label}</option>)}</optgroup>
            <option value="table">Line-item table</option>
            {draft.line_items.map((_, index) => <optgroup key={index} label={`Existing line ${index + 1}`}>{REGION_LINES.map(([key, label]) => <option key={key} value={`line:${index}:${key}`}>Line {index + 1} · {label}</option>)}</optgroup>)}
          </select>
        </label>
      </fieldset>
      <Button size="sm" disabled={locked || settingsStale || imageError || !rect || !validRect(rect) || !history.data?.eligible || history.isError || history.isFetching} onClick={startRead}>Re-read selected area</Button>
    </>}
    {readUncertain && <div className="space-y-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm">
      <p>The read outcome is unconfirmed. Check the original request before starting another read.</p>
      <Button variant="secondary" size="sm" disabled={checking || create.isPending} onClick={() => void checkRead()}>Check re-read outcome</Button>
      {readMissing && readIntent && <Button variant="secondary" size="sm" disabled={checking || create.isPending} onClick={() => create.mutate(readIntent)}>Retry original read</Button>}
    </div>}
    {selectedId && detail.isError && <ErrorState title="Couldn’t load the re-read result" onRetry={() => void detail.refetch()} />}
    {read && <section aria-label="OCR re-read comparison" className="space-y-3 rounded-lg border border-slate-200 p-3">
      <h4 className="font-semibold">Re-read result</h4>
      {read.status === "queued" || read.status === "running" ? <p role="status">{read.status === "queued" ? "Queued for the reader…" : "Reading the selected area…"} No capture values have changed.</p> : null}
      {read.status === "ready" && <p role="status" className="text-sm text-slate-600">Reading completed. Review the suggestions below; no capture values have changed.</p>}
      {(read.status === "failed" || read.status === "stale") && <p role="alert" className="text-sm text-amber-700">{read.error_detail || "This result can no longer be applied. Refresh the capture before making another selection."}</p>}
      {read.result && <>
        <details><summary className="cursor-pointer text-sm text-slate-600">Text read from this selection</summary><pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded bg-slate-50 p-2 text-xs">{read.result.text || "No text was recognized in this area."}</pre></details>
        <p className="text-xs text-slate-500">Reader: {read.result.engine}. Suggestions need verification against the original; no accuracy percentage is claimed.</p>
        {read.result.warnings?.length > 0 && <ul className="list-inside list-disc text-xs text-amber-700">{read.result.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>}
      </>}
      {read.status === "ready" && read.result && <>
        <p className="text-xs text-slate-600">Choose only the changes you want. Previously corrected values are marked and remain unchanged unless explicitly selected.</p>
        {proposed.map((field) => {
          const key = fieldKey(field.field, field.line_index), label = `${field.line_index == null ? "" : `Line ${field.line_index + 1} · `}${LABELS[field.field] ?? field.field}`;
          return <div key={key} className="space-y-2 rounded border border-slate-200 p-2">
            <label className="flex items-center gap-2 text-sm font-medium"><input type="checkbox" aria-label={`Apply ${label}`} disabled={locked || detail.isFetching} checked={!!chosen[key]} onChange={(event) => setChosen({ ...chosen, [key]: event.target.checked })} />{label}</label>
            <p className="text-xs text-slate-500">Saved value: <span className="font-medium">{currentRegionValue(saved, field.field, field.line_index) || "(empty)"}</span>{reviewed.has(key) && <span className="ml-2 text-emerald-700">Previously corrected</span>}</p>
            <label className="block text-xs text-slate-600">Proposed value<input className="input mt-1" aria-label={`Proposed ${label}`} disabled={locked} value={values[key] ?? field.value} onChange={(event) => setValues({ ...values, [key]: event.target.value })} maxLength={500} /></label>
          </div>;
        })}
        {read.result.rows.map((row, index) => {
          const targetValue = rowTargets[index] ?? (index < saved.line_items.length ? String(index) : "append");
          const available = REGION_LINES.filter(([key]) => row[key] != null).map(([key]) => key);
          return <div key={index} className="space-y-2 rounded border border-slate-200 p-2" aria-label={`Proposed row ${index + 1}`}>
            <label className="flex items-center gap-2 text-sm font-medium"><input type="checkbox" aria-label={`Select proposed row ${index + 1}`} disabled={locked} checked={available.length > 0 && available.every((field) => rowFields[index]?.includes(field))} onChange={(event) => setRowFields({ ...rowFields, [index]: event.target.checked ? available : [] })} />Proposed row {index + 1}</label>
            <select className="input" aria-label={`Destination for proposed row ${index + 1}`} disabled={locked} value={targetValue} onChange={(event) => { setRowTargets({ ...rowTargets, [index]: event.target.value }); setRowFields({ ...rowFields, [index]: [] }); }}>
              {saved.line_items.map((_, line) => <option key={line} value={line}>Existing line {line + 1}</option>)}<option value="append">Append as a new line</option>
            </select>
            <div className="space-y-1">{REGION_LINES.filter(([key]) => row[key] != null).map(([key, label]) => <label key={key} className="flex items-start gap-2 text-xs text-slate-600">
              <input className="mt-0.5" type="checkbox" aria-label={`Apply row ${index + 1} ${label}`} disabled={locked} checked={rowFields[index]?.includes(key) ?? false} onChange={(event) => setRowFields({ ...rowFields, [index]: event.target.checked ? [...(rowFields[index] ?? []), key] : (rowFields[index] ?? []).filter((field) => field !== key) })} />
              <span>{label}: <strong>{row[key]}</strong>{targetValue !== "append" && <> · saved {currentRegionValue(saved, key, Number(targetValue)) || "(empty)"}{reviewed.has(fieldKey(key, Number(targetValue))) && <span className="text-emerald-700"> · previously corrected</span>}</>}</span>
            </label>)}</div>
          </div>;
        })}
        {!proposed.length && !read.result.rows.length && <p className="text-sm text-amber-700">No usable field or row suggestion was found. Inspect the text above, adjust the area or edit the capture manually.</p>}
        {dirty && <p role="status" className="text-sm text-amber-700">Save your existing corrections before applying re-read changes, then refresh this comparison. Your unsaved edits are kept.</p>}
        {appendIncomplete && <p className="text-sm text-amber-700">A new line needs Description, Quantity, Unit price and Net amount selected.</p>}
        {touchesReviewed && <label className="flex items-start gap-2 text-sm text-amber-800"><input type="checkbox" aria-label="Replace only the previously corrected values I selected" disabled={locked} checked={overwriteReviewed} onChange={(event) => setOverwriteReviewed(event.target.checked)} />Replace only the previously corrected values I selected; keep all other corrections.</label>}
        <div className="flex flex-wrap gap-2"><Button size="sm" disabled={!canApply} onClick={applySelected}>Apply selected changes</Button>
          <Button variant="secondary" size="sm" disabled={locked || detail.isFetching} onClick={() => void refreshComparison()}>Refresh comparison</Button></div>
      </>}
      {read.status === "applied" && !applyUncertain && <p role="status" className="text-sm text-emerald-700">This selection has already been applied. Its original result is retained.</p>}
    </section>}
    {applyUncertain && <div className="space-y-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm">
      <p>{knownApplied ? "The changes are recorded. Reload the saved result without applying again." : "The apply outcome is unconfirmed. Check the saved result before requesting another change."}</p>
      <Button variant="secondary" size="sm" disabled={checking || apply.isPending} onClick={() => void checkApplied()}>Check applied result</Button>
      {!knownApplied && applyMissing && applyIntent && selectedId && <Button variant="secondary" size="sm" disabled={checking || apply.isPending} onClick={() => apply.mutate({ id: selectedId, request: applyIntent })}>Retry original application</Button>}
    </div>}
    {history.data && history.data.items.length > 0 && <div className="space-y-2 border-t border-slate-100 pt-3"><h4 className="text-xs font-medium text-slate-600">Recent re-reads</h4>
      <ul className="space-y-1">{history.data.items.map((item) => <li key={item.id}><Button variant="ghost" size="sm" disabled={locked || !terminal(read) && !!selectedId} onClick={() => { setSelectedId(item.id); setReadIntent(null); setApplyIntent(null); setError(null); setNotice(null); }}>Inspect page {item.request.page} · {read?.id === item.id ? read.status : item.status}</Button></li>)}</ul>
    </div>}
  </section>;
}
