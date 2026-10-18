import axios from "axios";
import { api, apiError, tokenStore } from "./api";
import type { BatchUploadAccepted, BatchUploadOutcome } from "./types";

export type CaptureUploadResult = BatchUploadAccepted & { quotaError?: string; requestError?: string };
export type CaptureScope = { userId: string; orgId: string };
type CaptureCommand = { key: string; filename: string; fingerprint: string };
type Committed = { extraction_run_id: string | null; status: string };
const slot = (s: CaptureScope) => `invoiceiq:capture-commands:${s.userId}:${s.orgId}`;
const headers = (s: CaptureScope) => ({ "X-Expected-User-ID": s.userId, "X-Expected-Org-ID": s.orgId });

export function savedCaptures(scope: CaptureScope): CaptureCommand[] {
  try {
    const parsed: unknown = JSON.parse(sessionStorage.getItem(slot(scope)) ?? "[]");
    if (!Array.isArray(parsed) || parsed.length > 25 || parsed.some(c =>
      !c || typeof c.key !== "string" || !/^[A-Za-z0-9._:-]{8,120}$/.test(c.key) ||
      typeof c.filename !== "string" || typeof c.fingerprint !== "string")) throw new Error();
    if (new Set(parsed.map(c => c.key)).size !== parsed.length) throw new Error();
    return parsed;
  } catch {
    throw new Error("Saved upload recovery information is unavailable. Check Captures before starting another upload.");
  }
}

function save(scope: CaptureScope, commands: CaptureCommand[]) {
  try { sessionStorage.setItem(slot(scope), JSON.stringify(commands)); }
  catch { throw new Error("Could not update browser upload recovery information. Free browser storage, then check Captures and retry checking results."); }
}
function forget(scope: CaptureScope, command: CaptureCommand) {
  save(scope, savedCaptures(scope).filter(c => c.key !== command.key));
}
function assertSession(generation: number) {
  if (tokenStore.generation() !== generation) throw new axios.CanceledError("Account or workspace changed. Reload uploads.");
}
function summary(outcomes: BatchUploadOutcome[]): BatchUploadAccepted {
  const accepted = outcomes.filter(o => o.accepted).length;
  return { accepted, rejected: outcomes.length - accepted, outcomes };
}
function unknown(command: CaptureCommand, unavailable = false): BatchUploadOutcome {
  return { filename: command.filename, accepted: false, code: "upload_outcome_unknown",
    message: unavailable ? "Could not check this upload. Reconnect and check its result before retrying."
      : "No committed result was found yet. Retry or reselect this original file to keep the same upload request." };
}

async function check(command: CaptureCommand, scope: CaptureScope, generation: number): Promise<BatchUploadOutcome | null> {
  assertSession(generation);
  try {
    const { data } = await api.get<Committed>(`/invoices/upload-commands/${encodeURIComponent(command.key)}`, { headers: headers(scope) });
    assertSession(generation);
    if (data.status === "retired" && data.extraction_run_id === null) {
      forget(scope, command);
      return { filename: command.filename, accepted: false, code: "capture_command_retired",
        message: "This upload was accepted, but its capture is no longer available. Check Captures before intentionally uploading it again." };
    }
    if (typeof data.extraction_run_id !== "string" || !["queued", "running", "parsed", "failed", "saved"].includes(data.status)) throw new Error("Invalid upload result");
    forget(scope, command);
    return { filename: command.filename, accepted: true, extraction_run_id: data.extraction_run_id };
  } catch (error) {
    assertSession(generation);
    if (axios.isAxiosError(error) && error.response?.status === 404) return null;
    throw error;
  }
}

export async function checkCaptureResults(scope: CaptureScope): Promise<BatchUploadAccepted> {
  const generation = tokenStore.generation();
  const outcomes: BatchUploadOutcome[] = [];
  for (const command of savedCaptures(scope)) {
    try { outcomes.push(await check(command, scope, generation) ?? unknown(command)); }
    catch { assertSession(generation); outcomes.push(unknown(command, true)); }
  }
  return summary(outcomes);
}

function sameSource(previous: string, chosen: string): boolean {
  try {
    const { override: wasOverride, ...wasSource } = JSON.parse(previous);
    const { override: nowOverride, ...nowSource } = JSON.parse(chosen);
    return typeof wasOverride === "boolean" && typeof nowOverride === "boolean" &&
      JSON.stringify(wasSource) === JSON.stringify(nowSource);
  } catch { return false; }
}

/** One command per source, sequential bounded hashing; never store file bytes. */
export async function uploadCaptures(files: File[], override: boolean, scope: CaptureScope,
  policy: { max_bytes: number; max_files: number; max_batch_bytes: number }): Promise<CaptureUploadResult> {
  const generation = tokenStore.generation();
  const previous = savedCaptures(scope);
  const retained = [...previous];
  const used = new Set<string>();
  const commands: CaptureCommand[] = [];
  if (!files.length || files.length > policy.max_files) throw new Error(`Choose between 1 and ${policy.max_files} files.`);
  for (const file of files) {
    if (file.size > policy.max_bytes) throw new Error("A file exceeds the upload limit. Choose a smaller original before uploading.");
    assertSession(generation);
    const bytes = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
    const sha256 = Array.from(new Uint8Array(bytes), b => b.toString(16).padStart(2, "0")).join("");
    assertSession(generation);
    const fingerprint = JSON.stringify({ sha256, filename: file.name, size: file.size, mime: file.type, override });
    let command = previous.find(c => sameSource(c.fingerprint, fingerprint) && !used.has(c.key));
    if (!command) {
      if (previous.length) throw new Error("An earlier upload has an unresolved result. Check it or reselect its original files before starting a different upload.");
      command = { key: crypto.randomUUID(), filename: file.name, fingerprint };
      retained.push(command);
    }
    used.add(command.key);
    commands.push(command);
  }
  assertSession(generation);
  save(scope, retained);
  const outcomes: (BatchUploadOutcome | undefined)[] = new Array(files.length);
  const submit: number[] = [];
  for (let i = 0; i < commands.length; i++) {
    if (!previous.some(c => c.key === commands[i].key)) { submit.push(i); continue; }
    try {
      const result = await check(commands[i], scope, generation);
      if (result) outcomes[i] = result;
      else submit.push(i);
    } catch { assertSession(generation); outcomes[i] = unknown(commands[i], true); }
  }
  let quotaError: string | undefined;
  let requestError: string | undefined;
  // A reselected source replays its retained explicit override decision. It
  // must not silently change options because a page refresh reset the form.
  for (const groupOverride of [false, true]) {
    const group = submit.filter(i => JSON.parse(commands[i].fingerprint).override === groupOverride);
    if (!group.length) continue;
    const chunks: number[][] = [];
    let bytes = 0;
    for (const i of group) {
      if (!chunks.length || bytes + files[i].size > policy.max_batch_bytes) {
        chunks.push([]);
        bytes = 0;
      }
      chunks[chunks.length - 1].push(i);
      bytes += files[i].size;
    }
    for (const group of chunks) {
      const form = new FormData();
      for (const i of group) form.append("files", files[i]);
      form.append("command_keys", JSON.stringify(group.map(i => commands[i].key)));
      assertSession(generation);
      try {
        const { data } = await api.post<BatchUploadAccepted>(`/invoices/upload/batch${groupOverride ? "?override=true" : ""}`, form, { headers: headers(scope) });
        assertSession(generation);
        if (!Array.isArray(data.outcomes) || data.outcomes.length !== group.length || data.outcomes.some((o, n) =>
          o.filename !== commands[group[n]].filename || typeof o.accepted !== "boolean" ||
          (o.accepted && typeof o.extraction_run_id !== "string"))) throw new Error("Incomplete upload response");
        for (let n = 0; n < group.length; n++) {
          const i = group[n];
          forget(scope, commands[i]);
          outcomes[i] = data.outcomes[n];
        }
      } catch (error) {
        assertSession(generation);
        if (axios.isAxiosError(error) && error.response?.status === 402) quotaError = apiError(error);
        if (axios.isAxiosError(error) && ([408, 413].includes(error.response?.status ?? 0) ||
            error.response?.data?.code === "upload_busy")) requestError = apiError(error);
        for (const i of group) {
          try { outcomes[i] = await check(commands[i], scope, generation) ?? unknown(commands[i]); }
          catch { assertSession(generation); outcomes[i] = unknown(commands[i], true); }
        }
      }
    }
  }
  return { ...summary(outcomes.map((o, i) => o ?? unknown(commands[i]))), quotaError, requestError };
}
