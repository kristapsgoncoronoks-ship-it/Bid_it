import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, apiError } from "../lib/api";
import { Badge } from "../components/ui";
import { shortDate } from "../lib/format";

/** CRM light (WO-H): one customer, everything known about the relationship.
 * The timeline is DERIVED — notes are the only hand-written part; offers,
 * projects, invoices and sent emails appear because they happened. */

interface Note {
  id: string;
  body: string;
  created_by: string | null;
  created_at: string;
}

interface TimelineEvent {
  at: string;
  kind: string;
  title: string;
  ref: string | null;
}

interface CustomerRow {
  id: string;
  name: string;
  email: string | null;
  lifecycle: string;
  vat_number?: string | null;
  city?: string | null;
  country?: string | null;
}

const LIFECYCLES = ["prospect", "active", "dormant", "lost"] as const;
const LIFECYCLE_TONE: Record<string, "success" | "neutral" | "warning" | "danger"> = {
  prospect: "warning",
  active: "success",
  dormant: "neutral",
  lost: "danger",
};
const KIND_ICON: Record<string, string> = {
  note: "✎",
  offer: "▤",
  project: "▦",
  invoice: "€",
  email: "✉",
};

export default function CustomerDetail() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  const customers = useQuery<CustomerRow[]>({
    queryKey: ["customers"],
    queryFn: async () => (await api.get("/customers")).data,
  });
  const customer = (Array.isArray(customers.data) ? customers.data : []).find(
    (c) => c.id === id,
  );

  const notes = useQuery<Note[]>({
    queryKey: ["customer-notes", id],
    queryFn: async () => (await api.get(`/customers/${id}/notes`)).data,
  });
  const timeline = useQuery<{ events: TimelineEvent[] }>({
    queryKey: ["customer-timeline", id],
    queryFn: async () => (await api.get(`/customers/${id}/timeline`)).data,
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["customer-notes", id] });
    qc.invalidateQueries({ queryKey: ["customer-timeline", id] });
  };

  const addNote = useMutation({
    mutationFn: async () =>
      (await api.post(`/customers/${id}/notes`, { body: draft })).data,
    onSuccess: () => {
      setDraft("");
      setErr(null);
      refresh();
    },
    onError: (e) => setErr(apiError(e)),
  });
  const deleteNote = useMutation({
    mutationFn: async (noteId: string) => api.delete(`/customers/${id}/notes/${noteId}`),
    onSuccess: () => {
      setErr(null);
      refresh();
    },
    onError: (e) => setErr(apiError(e)),
  });
  const setLifecycle = useMutation({
    mutationFn: async (lifecycle: string) =>
      (await api.put(`/customers/${id}/lifecycle`, { lifecycle })).data,
    onSuccess: () => {
      setErr(null);
      qc.invalidateQueries({ queryKey: ["customers"] });
    },
    onError: (e) => setErr(apiError(e)),
  });

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            {customer ? customer.name : "Customer"}
          </h1>
          <p className="text-sm text-slate-500">
            {customer?.email || "no email on record"}
            {customer?.vat_number ? ` · VAT ${customer.vat_number}` : ""}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {customer && (
            <>
              <Badge tone={LIFECYCLE_TONE[customer.lifecycle] ?? "neutral"}>
                {customer.lifecycle}
              </Badge>
              <select
                className="input w-36"
                value={customer.lifecycle}
                disabled={setLifecycle.isPending}
                onChange={(e) => setLifecycle.mutate(e.target.value)}
              >
                {LIFECYCLES.map((l) => (
                  <option key={l} value={l}>
                    {l}
                  </option>
                ))}
              </select>
            </>
          )}
          <Link to="/customers" className="btn-secondary shrink-0">
            All customers
          </Link>
        </div>
      </div>

      {err && (
        <div className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {err}
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <div className="card space-y-3 p-6">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">Notes</h2>
          <div className="flex gap-2">
            <input
              className="input flex-1"
              placeholder="Prefers morning calls; gate code 4711…"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && draft.trim()) addNote.mutate();
              }}
            />
            <button
              className="btn-primary"
              disabled={addNote.isPending || !draft.trim()}
              onClick={() => addNote.mutate()}
            >
              Add note
            </button>
          </div>
          {(notes.data ?? []).length === 0 ? (
            <p className="text-sm text-slate-400">Nothing noted yet.</p>
          ) : (
            <ul className="divide-y divide-slate-100">
              {(notes.data ?? []).map((n) => (
                <li key={n.id} className="flex items-start justify-between gap-3 py-2">
                  <div>
                    <p className="text-sm text-slate-700">{n.body}</p>
                    <p className="text-xs text-slate-400">
                      {shortDate(n.created_at)}
                      {n.created_by ? ` · ${n.created_by}` : ""}
                    </p>
                  </div>
                  <button
                    className="btn-ghost text-xs text-rose-500"
                    onClick={() => deleteNote.mutate(n.id)}
                  >
                    Delete
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="card space-y-3 p-6">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            Activity
          </h2>
          <p className="text-xs text-slate-400">
            Derived from what actually happened — offers, projects, invoices,
            emails and notes. Nobody maintains this feed.
          </p>
          {(timeline.data?.events ?? []).length === 0 ? (
            <p className="text-sm text-slate-400">No activity recorded yet.</p>
          ) : (
            <ul className="divide-y divide-slate-100">
              {(timeline.data?.events ?? []).map((e, i) => (
                <li key={i} className="flex items-start gap-3 py-2">
                  <span className="mt-0.5 w-5 text-center text-slate-400" aria-hidden>
                    {KIND_ICON[e.kind] ?? "•"}
                  </span>
                  <div className="min-w-0">
                    <p className="truncate text-sm text-slate-700">
                      {e.ref ? (
                        <Link to={e.ref} className="hover:underline">
                          {e.title}
                        </Link>
                      ) : (
                        e.title
                      )}
                    </p>
                    <p className="text-xs text-slate-400">{shortDate(e.at)}</p>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
