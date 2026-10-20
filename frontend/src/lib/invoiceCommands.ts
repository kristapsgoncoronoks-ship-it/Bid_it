import axios from "axios";
import { api, tokenStore } from "./api";

type Scope = { userId: string; orgId: string };

/** Validate and adapt an opt-in caller's response before forgetting its key.
 * Lookup envelopes and direct write results may have different shapes.
 */
export type CommandResponseAdapter<T> = (value: unknown, commandKey: string, source: "lookup" | "write") => T;

function commandSlot(path: string, scope: Scope): string {
  return `invoiceiq:command:${scope.userId}:${scope.orgId}:${path}`;
}

function continuationGuard(signal?: AbortSignal): () => void {
  const generation = tokenStore.generation();
  const token = tokenStore.get();
  return () => {
    if (signal?.aborted) {
      throw new axios.CanceledError("Command interrupted. Check any saved reference before starting another request.");
    }
    if (generation !== tokenStore.generation() || token !== tokenStore.get()) {
      throw new axios.CanceledError("Session changed; the previous command cannot continue in this session.");
    }
  };
}

export function hasSavedInvoiceCommand(path: string, scope: Scope): boolean {
  return sessionStorage.getItem(commandSlot(path, scope)) !== null;
}

export async function checkSavedInvoiceCommand<T>(
  path: string,
  scope: Scope,
  lookupPath: (key: string) => string,
  adaptResponse?: CommandResponseAdapter<T>,
  signal?: AbortSignal,
): Promise<T | null> {
  const assertCanContinue = continuationGuard(signal);
  assertCanContinue();
  const slot = commandSlot(path, scope);
  const stored = sessionStorage.getItem(slot);
  if (!stored) return null;
  let command: { key?: unknown } | null;
  try { command = JSON.parse(stored); }
  catch { throw new Error("The previous request is unreadable. Check the relevant record list and ask an administrator before retrying."); }
  if (!command || typeof command.key !== "string") throw new Error("The previous request is unreadable. Check the relevant record list and ask an administrator before retrying.");
  try {
    const result = await api.get<T>(lookupPath(command.key), { signal, headers: {
      "X-Expected-Org-ID": scope.orgId, "X-Expected-User-ID": scope.userId,
    } });
    assertCanContinue();
    const value = adaptResponse ? adaptResponse(result.data, command.key, "lookup") : result.data;
    sessionStorage.removeItem(slot);
    return value;
  } catch (error) {
    assertCanContinue();
    if (axios.isAxiosError(error) && error.response?.status === 404) return null;
    throw error;
  }
}

/** Keep a command key across retries and refreshes, scoped to account + company.
 * Invoice forms store only key/digest. Cash commands may retain their small
 * amount/date/reason envelope to replay the exact request after a refresh.
 */
export async function invoiceCommand<T>(
  path: string,
  payload: unknown,
  scope: Scope,
  lookupPath: (key: string) => string,
  stableKey?: string,
  storagePath = path,
  retainCashPayload = false,
  fingerprintPayload?: unknown,
  method: "post" | "delete" = "post",
  adaptResponse?: CommandResponseAdapter<T>,
  signal?: AbortSignal,
): Promise<T> {
  const assertCanContinue = continuationGuard(signal);
  assertCanContinue();
  const slot = commandSlot(storagePath, scope);
  const digestBytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify(fingerprintPayload ?? (retainCashPayload ? { path, payload } : payload))));
  // Hashing yields before any request is sent. Do not attach a new session's
  // bearer token to an intention captured by the previous account or company.
  assertCanContinue();
  const digest = Array.from(new Uint8Array(digestBytes), (b) => b.toString(16).padStart(2, "0")).join("");
  const stored = sessionStorage.getItem(slot);
  let command: { key: string; digest: string; path?: string; payload?: unknown };
  try {
    command = stored ? JSON.parse(stored) : { key: stableKey ?? crypto.randomUUID(), digest,
      ...(retainCashPayload ? { path, payload } : {}) };
    if (typeof command.key !== "string" || typeof command.digest !== "string") throw new Error();
  } catch {
    throw new Error("The saved request identity is unreadable. Check the relevant record list and its outcome before retrying.");
  }
  const headers = { "Idempotency-Key": command.key, "X-Expected-Org-ID": scope.orgId, "X-Expected-User-ID": scope.userId };
  // A changed form must not replace an unresolved command with a new financial write.
  if (command.digest !== digest) {
    throw new Error("A previous request has an unresolved outcome. Check its result or retry the original inputs before starting another financial request.");
  }
  sessionStorage.setItem(slot, JSON.stringify(command));
  if (stored) {
    try {
      const result = await api.get<T>(lookupPath(command.key), { headers, signal });
      assertCanContinue();
      const value = adaptResponse ? adaptResponse(result.data, command.key, "lookup") : result.data;
      sessionStorage.removeItem(slot);
      return value;
    } catch (error) {
      assertCanContinue();
      if (!axios.isAxiosError(error) || error.response?.status !== 404) {
        throw new Error("Could not check the previous request. Keep this form and retry when connected; no new command was started.");
      }
    }
  }
  // A saved command lookup can yield before reporting 404. Recheck its session
  // before replaying the original write under the same captured intention.
  assertCanContinue();
  try {
    const result = method === "delete"
      ? await api.delete<T>(path, { headers, signal })
      : await api.post<T>(path, payload, { headers, signal });
    assertCanContinue();
    const value = adaptResponse ? adaptResponse(result.data, command.key, "write") : result.data;
    sessionStorage.removeItem(slot);
    return value;
  } catch (error) {
    assertCanContinue();
    // A definite validation/permission refusal permits correction; transport or
    // server failures retain the key because the transaction may have committed.
    if (axios.isAxiosError(error) && error.response && [400, 401, 403, 404, 413, 415, 422].includes(error.response.status)) {
      sessionStorage.removeItem(slot);
      throw error;
    }
    if (axios.isAxiosError(error) && error.response?.status === 409 &&
        ((String(error.response.data?.code ?? "").startsWith("payment_") || String(error.response.data?.code ?? "").startsWith("credit_")) || ["command_scope_changed", "expense_selection_conflict", "expense_import_currency_conflict"].includes(String(error.response.data?.code ?? "")))) {
      sessionStorage.removeItem(slot);
      throw error;
    }
    // A response canceled by an identity change must not start a status lookup
    // under the replacement session or discard the original command reference.
    assertCanContinue();
    try {
      const result = await api.get<T>(lookupPath(command.key), { headers, signal });
      assertCanContinue();
      const value = adaptResponse ? adaptResponse(result.data, command.key, "lookup") : result.data;
      sessionStorage.removeItem(slot);
      return value;
    } catch {
      assertCanContinue();
      throw new Error("Request outcome is unknown. Keep the same inputs and retry to check the saved command before starting another financial request.");
    }
  }
}

export async function retrySavedCashCommand<T>(storagePath: string, scope: Scope, lookup: (key: string) => string): Promise<T> {
  const stored = sessionStorage.getItem(commandSlot(storagePath, scope));
  if (!stored) throw new Error("No saved cash command remains. Refresh the invoice and review its payment history.");
  const request = JSON.parse(stored) as { path?: unknown; payload?: unknown };
  const invoicePath = storagePath.replace(/\/cash-command$/, "");
  if (typeof request.path !== "string" || !request.path.startsWith(invoicePath) ||
      !/^\/payments(?:\/[A-Za-z0-9-]+\/reverse)?$/.test(request.path.slice(invoicePath.length)) ||
      !request.payload || typeof request.payload !== "object") {
    throw new Error("The saved cash request is unreadable. Review payment history before recording cash.");
  }
  return invoiceCommand<T>(request.path, request.payload, scope, lookup, undefined, storagePath, true);
}

export function deliveryOutcome(status: string, error: string | null): string {
  switch (status) {
    case "sent": return "Accepted by the mail relay; recipient delivery is not confirmed.";
    case "queued": return "Delivery queued for the worker; not yet sent.";
    case "sending": return "Delivery is being attempted; not yet confirmed.";
    case "blocked": return error || "Delivery blocked. Review the mail configuration.";
    case "unknown": return error || "Delivery outcome unknown. Check the relay before resending.";
    case "failed": return error || "Delivery failed. Review the mail log before retrying.";
    default: return "Recorded only; no delivery was queued. Review the mail configuration.";
  }
}

/** Replay only the saved selected-expense request; never rebuild its inputs. */
export async function retrySavedExpenseReport<T>(scope: Scope): Promise<T> {
  const slot = "/expenses/from-selected";
  const stored = sessionStorage.getItem(commandSlot(slot, scope));
  if (!stored) throw new Error("No previous report request remains. Refresh the report list.");
  let saved: { path?: unknown; payload?: unknown } | null;
  try { saved = JSON.parse(stored); }
  catch { throw new Error("The previous report request is unreadable. Check the report list and ask an administrator before retrying."); }
  if (!saved) throw new Error("The previous report request is unreadable. Check the report list and ask an administrator before retrying.");
  if (saved.path !== "/expenses" || !saved.payload || typeof saved.payload !== "object") {
    throw new Error("The previous report inputs are unavailable. Check its outcome before creating another report.");
  }
  return invoiceCommand<T>("/expenses", saved.payload, scope,
    (key) => `/expenses/commands/${key}/result`, undefined, slot, true);
}
