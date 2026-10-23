import { useMutation, useQuery } from "@tanstack/react-query";
import axios from "axios";
import { useEffect, useRef, useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { Button, ErrorState, Skeleton } from "./ui";
import { api, apiError, isCanceledRequest, tokenStore } from "../lib/api";
import { formatDateTime } from "../lib/format";

type Provider = "google" | "outlook";
interface Connection {
  id: string;
  status: string;
  calendar_name: string | null;
  last_synced_at: string | null;
  error: string | null;
  pending: number;
  failed: number;
}
interface CalendarProvider {
  provider: Provider;
  configured: boolean;
  reason: string | null;
  connection: Connection | null;
}
interface Connections { providers: CalendarProvider[] }

const NAMES: Record<Provider, string> = { google: "Google Calendar", outlook: "Outlook Calendar" };
const LABELS: Record<string, string> = {
  connected: "Connected", active: "Connected", ready: "Connected", synced: "Synced",
  queued: "Sync queued", pending: "Sync pending", syncing: "Syncing",
  failed: "Needs attention", needs_reconnect: "Reconnect required",
  disconnected: "Disconnected", setup_unknown: "Calendar setup needs checking",
  authorizing: "Authorization not completed", connecting: "Setting up calendar",
  needs_configuration: "Administrator configuration required",
};

/** Account-owned calendar connections, separate from the polling ICS feed. */
export function CalendarConnections({ disabled = false }: { disabled?: boolean }) {
  const { user, org } = useAuth();
  const [open, setOpen] = useState(() => new URLSearchParams(window.location.search).has("calendar_sync"));
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const scope = `${user?.id}:${org?.id}:${tokenStore.generation()}`;
  const currentScope = useRef(scope);
  currentScope.current = scope;
  useEffect(() => { currentScope.current = scope; return () => { currentScope.current = ""; }; }, [scope]);
  useEffect(() => { setError(null); setNotice(null); setUncertain(false); }, [scope]);
  const query = useQuery<Connections>({
    queryKey: ["calendar-connections", user?.id, org?.id],
    enabled: open,
    queryFn: async ({ signal }) => {
      const data = (await api.get("/schedule/calendar-connections", { signal })).data;
      if (!Array.isArray(data?.providers) || data.providers.length !== 2 ||
          !data.providers.every((p: CalendarProvider) => p.provider === "google" || p.provider === "outlook") ||
          new Set(data.providers.map((p: CalendarProvider) => p.provider)).size !== 2) {
        throw new Error("Calendar connection status is unavailable.");
      }
      return data;
    },
    refetchInterval: (q) => q.state.data?.providers.some((p) => (p.connection?.pending ?? 0) > 0) ? 5000 : false,
  });
  const change = useMutation({
    mutationFn: async ({ provider, action, sentScope }: { provider: Provider; action: "connect" | "retry" | "disconnect"; sentScope: string }) => {
      if (currentScope.current !== sentScope) throw new Error("The workspace changed. Refresh calendar status.");
      const path = `/schedule/calendar-connections/${provider}`;
      const response = action === "disconnect" ? await api.delete(path) : await api.post(`${path}/${action}`, {}, { withCredentials: action === "connect" });
      return { action, provider, sentScope, data: response.data };
    },
    onSuccess: async ({ action, provider, sentScope, data }) => {
      if (currentScope.current !== sentScope) return;
      setError(null);
      if (action === "connect") {
        let url: URL;
        try { url = new URL(data.authorization_url); }
        catch { setUncertain(true); setError("The calendar authorization address was unavailable. Refresh status before trying again."); return; }
        const expectedHost = provider === "google" ? "accounts.google.com" : "login.microsoftonline.com";
        if (url.protocol !== "https:" || url.hostname !== expectedHost || url.username || url.password) {
          setUncertain(true); setError("The calendar authorization address was invalid. Reload connection status before trying again.");
          return;
        }
        window.location.assign(url.href);
        return;
      }
      setNotice(action === "disconnect"
        ? "Disconnected. Previously exported events remain in your calendar; you can remove that calendar there."
        : "Sync requested. The status below shows when the calendar provider acknowledges the changes.");
      setUncertain(true);
      const result = await query.refetch();
      if (currentScope.current !== sentScope) return;
      if (result.isError) setError("The request was accepted, but current status could not be loaded. Refresh status before trying another action.");
      else setUncertain(false);
    },
    onError: (e, { sentScope }) => {
      if (currentScope.current !== sentScope || isCanceledRequest(e)) return;
      setError(apiError(e));
      if (axios.isAxiosError(e) && (!e.response || e.response.status >= 500)) setUncertain(true);
    },
  });
  const busy = disabled || change.isPending || query.isFetching;
  const writeBusy = busy || uncertain;
  async function refresh() {
    const sentScope = scope;
    const result = await query.refetch();
    if (currentScope.current !== sentScope) return;
    if (result.isError) setError("Couldn’t refresh calendar status. Try again before requesting another change.");
    else { setError(null); setUncertain(false); }
  }

  return (
    <section className="card space-y-3 p-4" aria-label="Calendar connections">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-slate-700">Google & Outlook calendars</h2>
          <p className="text-xs text-slate-500">Push your assigned work to a separate calendar. Edit the schedule in Bid_it.</p>
        </div>
        <Button variant="secondary" size="sm" onClick={() => setOpen((v) => !v)}>{open ? "Hide connections" : "Calendar connections"}</Button>
      </div>
      {open && <>
        <p className="text-sm text-slate-600">Saving a change queues a calendar update. Provider and device delays can occur; check sync status below. Calendar sync sends no attendee invitations. Work-order email notices are controlled separately.</p>
        {disabled && <p role="status" className="text-sm text-amber-700">Finish or cancel your work-order edit before changing calendar connections.</p>}
        {error && <p role="alert" className="text-sm text-rose-700">{error}</p>}
        {uncertain && <p role="status" className="text-sm text-amber-700">Refresh calendar status before requesting another change.</p>}
        {notice && <p role="status" className="text-sm text-slate-600">{notice}</p>}
        {query.isPending && <Skeleton className="h-28 w-full" />}
        {query.isError && <ErrorState title="Couldn’t load calendar connections" onRetry={() => void refresh()} />}
        {!query.isError && query.data && <>
          <div className="grid gap-3 sm:grid-cols-2">
            {query.data.providers.map((provider) => {
              const connection = provider.connection;
              const linked = !!connection && connection.status !== "disconnected";
              return <div key={provider.provider} className="space-y-2 rounded-lg border border-slate-200 p-3" aria-label={NAMES[provider.provider]}>
                <h3 className="font-medium text-slate-800">{NAMES[provider.provider]}</h3>
                <p className="text-sm text-slate-600">{connection ? LABELS[connection.status] ?? "Status needs checking" : "Not connected"}</p>
                {!provider.configured && <p className="text-sm text-amber-700">{provider.reason || "An administrator needs to configure the calendar integration first."}</p>}
                {linked && <>
                  {connection.calendar_name && <p className="text-sm text-slate-600">{connection.calendar_name}</p>}
                  <p className="text-xs text-slate-500">{connection.pending} pending · {connection.failed} need attention</p>
                  <p className="text-xs text-slate-500">{connection.last_synced_at ? `Last provider acknowledgement: ${formatDateTime(connection.last_synced_at)}` : "No provider acknowledgement yet"}</p>
                  {connection.error && <p role="alert" className="text-sm text-rose-700">{connection.status === "needs_reconnect" ? "Calendar authorization needs renewing. Connect again to continue." : "The calendar update needs attention. Check and retry sync to recover."}</p>}
                </>}
                <div className="flex flex-wrap gap-2">
                  {(!linked || connection?.status === "needs_reconnect" || connection?.status === "authorizing") && <Button size="sm" disabled={writeBusy || !provider.configured} onClick={() => { setNotice(null); change.mutate({ provider: provider.provider, action: "connect", sentScope: scope }); }}>Connect {NAMES[provider.provider]}</Button>}
                  {linked && <>
                    <Button variant="secondary" size="sm" disabled={writeBusy || !provider.configured} onClick={() => { setNotice(null); change.mutate({ provider: provider.provider, action: "retry", sentScope: scope }); }}>Check & retry sync</Button>
                    <Button variant="ghost" size="sm" disabled={writeBusy} title="Stop future sync; existing calendar events remain" onClick={() => { setNotice(null); change.mutate({ provider: provider.provider, action: "disconnect", sentScope: scope }); }}>Disconnect {NAMES[provider.provider]}</Button>
                  </>}
                </div>
              </div>;
            })}
          </div>
          <Button variant="secondary" size="sm" disabled={busy} onClick={() => void refresh()}>Refresh calendar status</Button>
        </>}
      </>}
    </section>
  );
}
