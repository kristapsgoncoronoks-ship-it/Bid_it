import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { api, apiError, isCanceledRequest, tokenStore } from "../lib/api";
import { formatDateTime } from "../lib/format";
import { Button, ErrorState } from "./ui";

interface Notice {
  id: string;
  kind: string;
  status: string;
  error: string | null;
  sent_at: string | null;
  created_at: string;
  can_retry: boolean;
}
interface Notices { smtp_configured: boolean; items: Notice[] }

const STATES: Record<string, string> = {
  queued: "Queued", blocked: "Delivery not configured", sending: "Sending",
  sent: "Accepted by mail server", failed: "Delivery failed", unknown: "Delivery outcome unknown",
  recorded: "Recorded only; delivery unconfirmed", cancelled: "Cancelled", skipped: "Skipped",
};
const KINDS: Record<string, string> = {
  assignment: "Assignment change", assignment_reminder: "Upcoming-work reminder", client_notice: "Customer arrival notice",
};

/** A read-first delivery view; unknown POST outcomes recover through GET. */
export function AssignmentNotifications({ assignmentId, revision }: { assignmentId: string; revision: number }) {
  const { user, org } = useAuth();
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const scope = `${user?.id}:${org?.id}:${tokenStore.generation()}:${assignmentId}:${revision}`;
  const currentScope = useRef(scope);
  currentScope.current = scope;
  useEffect(() => { currentScope.current = scope; return () => { currentScope.current = ""; }; }, [scope]);
  useEffect(() => { setOpen(false); setError(null); setUncertain(false); }, [scope, assignmentId, revision]);
  const query = useQuery<Notices>({
    queryKey: ["assignment-notifications", user?.id, org?.id, assignmentId, revision],
    enabled: open,
    queryFn: async ({ signal }) => {
      const data = (await api.get(`/schedule/assignments/${assignmentId}/notifications`, { signal })).data;
      if (typeof data?.smtp_configured !== "boolean" || !Array.isArray(data.items)) throw new Error("Email status is unavailable.");
      return data;
    },
    refetchInterval: (q) => q.state.data?.items.some((n) => n.status === "queued" || n.status === "sending") ? 5000 : false,
  });
  async function check() {
    const sentScope = scope;
    const result = await query.refetch();
    if (currentScope.current !== sentScope) return;
    if (result.isError) setError("Couldn’t check email delivery. Try Check email status again.");
    else { setError(null); setUncertain(false); }
  }
  const retry = useMutation({
    mutationFn: async ({ messageId, sentScope }: { messageId: string; sentScope: string }) => {
      if (currentScope.current !== sentScope) throw new Error("The work order changed. Check email status again.");
      await api.post(`/schedule/assignments/${assignmentId}/notifications/${messageId}/retry`, {});
      return sentScope;
    },
    onSuccess: async (sentScope) => {
      if (currentScope.current !== sentScope) return;
      setUncertain(true);
      await check();
    },
    onError: (e, { sentScope }) => {
      if (currentScope.current !== sentScope || isCanceledRequest(e)) return;
      setError(apiError(e));
      setUncertain(true);
    },
  });

  return <div className="space-y-2 text-xs">
    <Button variant="ghost" size="sm" onClick={() => setOpen((v) => !v)}>{open ? "Hide email details" : "Email details"}</Button>
    {open && <section aria-label="Work-order email details" className="space-y-2 rounded-lg border border-slate-200 bg-white p-3">
      <p className="text-slate-500">Email status is separate from saving the work order and calendar sync. Mail-server acceptance does not prove the recipient has read it.</p>
      {query.isPending && <p role="status">Loading email status…</p>}
      {query.isError && <ErrorState title="Couldn’t load email status" onRetry={() => check()} />}
      {error && <p role="alert" className="text-rose-700">{error}</p>}
      {uncertain && <p role="status" className="text-amber-700">Check the current status before requesting another delivery attempt.</p>}
      {!query.isError && query.data && <>
        {!query.data.smtp_configured && <p className="text-amber-700">Email delivery is not configured. An administrator needs to enable the mail service.</p>}
        {query.data.items.length === 0 && <p>No email notice was recorded for this work order.</p>}
        <ul className="space-y-2">
          {query.data.items.map((notice) => <li key={notice.id} className="space-y-1 border-t border-slate-100 pt-2">
            <p className="text-slate-500">{KINDS[notice.kind] ?? "Work-order notice"}</p>
            <p className="font-medium">{STATES[notice.status] ?? "Status needs checking"}</p>
            <p className="text-slate-500">{formatDateTime(notice.sent_at || notice.created_at)}</p>
            {notice.error && <p className="text-rose-700">{notice.error}</p>}
            {notice.can_retry && <Button variant="secondary" size="sm" disabled={retry.isPending || query.isFetching || uncertain || !query.data.smtp_configured} onClick={() => retry.mutate({ messageId: notice.id, sentScope: scope })}>Retry email</Button>}
          </li>)}
        </ul>
      </>}
      <Button variant="secondary" size="sm" disabled={retry.isPending || query.isFetching} onClick={() => void check()}>Check email status</Button>
    </section>}
  </div>;
}
