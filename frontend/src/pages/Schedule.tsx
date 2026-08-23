import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Badge, Button, QueryState, Skeleton } from "../components/ui";
import { api, apiError } from "../lib/api";
import { useAuth } from "../auth/AuthContext";

/**
 * Work planning (WO-A, docs/design/work-calendar.md phase A).
 *
 * One page, two audiences, decided by what the server actually returns:
 * planners (invoice-write roles) get the whole workspace and the planning
 * form; everyone else automatically receives ONLY their own assignments —
 * the same screen IS "My work". The role split lives on the server; the
 * page renders whatever it is given and only hides the planning form when
 * the members picker is refused (403).
 *
 * Overlap warnings from a save are ADVISORY (the server never blocks a
 * double-booking) — they render as a notice, not an error.
 */

interface Assignment {
  id: string;
  project_id: string;
  assignee_user_id: string;
  assignee_email: string;
  starts_at: string;
  ends_at: string;
  all_day: boolean;
  status: string;
  note: string | null;
  created_by: string;
}

interface Member {
  user_id: string;
  email: string;
  name: string | null;
}

interface ProjectRow {
  id: string;
  code: string;
  name: string;
  status: string;
}

const DAY_MS = 86_400_000;

function startOfWeek(d: Date): Date {
  const x = new Date(d);
  x.setHours(0, 0, 0, 0);
  const dow = (x.getDay() + 6) % 7; // Monday-first
  return new Date(x.getTime() - dow * DAY_MS);
}

function fmtDay(d: Date): string {
  return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
}

function fmtTime(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

/** Your schedule on your phone: a per-person secret feed URL that Google,
 * Apple and Microsoft calendars all subscribe to (they poll us — the platform
 * makes no external calls). Regenerating kills the old link instantly. */
function PhoneSync() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  const feed = useQuery<{ token: string; path: string }>({
    queryKey: ["schedule-feed-token"],
    queryFn: async () => (await api.get("/schedule/feed-token")).data,
    enabled: open,
  });

  const regenerate = useMutation({
    mutationFn: async () => (await api.post("/schedule/feed-token/regenerate")).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["schedule-feed-token"] }),
  });

  const url = feed.data ? `${window.location.origin}${feed.data.path}` : null;

  return (
    <div className="card space-y-2 p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Your calendar on your phone
        </h2>
        <button className="btn-ghost text-xs" onClick={() => setOpen(!open)}>
          {open ? "Hide" : "Set up"}
        </button>
      </div>
      {open && (
        <div className="space-y-2 text-sm text-slate-600">
          <p>
            Subscribe once and your assignments appear in Google, Apple or
            Outlook calendar and stay updated. Anyone with this link can see
            your schedule — regenerate it to cut off an old link.
          </p>
          {url && (
            <div className="flex flex-wrap items-center gap-2">
              <code className="max-w-full overflow-x-auto rounded bg-slate-100 px-2 py-1 text-xs">
                {url}
              </code>
              <Button
                variant="secondary"
                onClick={() => {
                  navigator.clipboard.writeText(url);
                  setCopied(true);
                  setTimeout(() => setCopied(false), 2000);
                }}
              >
                {copied ? "Copied" : "Copy link"}
              </Button>
              <Button variant="secondary" disabled={regenerate.isPending} onClick={() => regenerate.mutate()}>
                Regenerate
              </Button>
              <a className="btn-ghost text-xs" href={`${url.replace(/^https?/, "webcal")}`}>
                Open in calendar app
              </a>
            </div>
          )}
          <p className="text-xs text-slate-400">
            Google: Other calendars → From URL. Apple: Add Subscription
            Calendar. Outlook: Add calendar → Subscribe from web.
          </p>
        </div>
      )}
    </div>
  );
}

const STATUS_TONE: Record<string, "neutral" | "info" | "success" | "danger"> = {
  planned: "neutral",
  confirmed: "info",
  done: "success",
  cancelled: "danger",
};

export default function Schedule() {
  const qc = useQueryClient();
  const { user } = useAuth();
  const [weekStart, setWeekStart] = useState(() => startOfWeek(new Date()));
  const [err, setErr] = useState<string | null>(null);
  const [overlapNote, setOverlapNote] = useState<string | null>(null);
  const [filterMember, setFilterMember] = useState("");
  const [filterProject, setFilterProject] = useState("");
  const [form, setForm] = useState<{
    project_id: string;
    assignee_user_id: string;
    date: string;
    start: string;
    end: string;
    all_day: boolean;
    note: string;
  } | null>(null);

  const weekEnd = new Date(weekStart.getTime() + 7 * DAY_MS);
  const days = useMemo(
    () => Array.from({ length: 7 }, (_, i) => new Date(weekStart.getTime() + i * DAY_MS)),
    [weekStart],
  );

  const assignments = useQuery<Assignment[]>({
    queryKey: [
      "assignments",
      weekStart.toISOString(),
      filterMember,
      filterProject,
    ],
    queryFn: async () =>
      (
        await api.get("/schedule/assignments", {
          params: {
            start: weekStart.toISOString(),
            end: weekEnd.toISOString(),
            ...(filterMember ? { assignee_user_id: filterMember } : {}),
            ...(filterProject ? { project_id: filterProject } : {}),
          },
        })
      ).data,
  });

  // 403 → not a planner → personal view (no picker, no form, no filters).
  const members = useQuery<Member[] | null>({
    queryKey: ["schedule-members"],
    queryFn: async () => {
      try {
        return (await api.get("/schedule/members")).data;
      } catch {
        return null;
      }
    },
  });
  const canPlan = !!members.data;

  const projects = useQuery<ProjectRow[]>({
    queryKey: ["projects"],
    queryFn: async () => (await api.get("/masters/projects")).data,
    enabled: canPlan,
  });

  const save = useMutation({
    mutationFn: async () => {
      if (!form) return null;
      const starts = form.all_day
        ? new Date(`${form.date}T00:00:00`)
        : new Date(`${form.date}T${form.start}:00`);
      const ends = form.all_day
        ? new Date(new Date(`${form.date}T00:00:00`).getTime() + DAY_MS)
        : new Date(`${form.date}T${form.end}:00`);
      return (
        await api.post("/schedule/assignments", {
          project_id: form.project_id,
          assignee_user_id: form.assignee_user_id,
          starts_at: starts.toISOString(),
          ends_at: ends.toISOString(),
          all_day: form.all_day,
          note: form.note || null,
        })
      ).data;
    },
    onSuccess: (data) => {
      setForm(null);
      setErr(null);
      setOverlapNote(
        data && data.overlaps.length > 0
          ? `Heads up: ${data.assignment.assignee_email} is also booked ${data.overlaps
              .map((o: Assignment) => `${fmtTime(o.starts_at)}–${fmtTime(o.ends_at)}`)
              .join(", ")} — saved anyway.`
          : null,
      );
      qc.invalidateQueries({ queryKey: ["assignments"] });
    },
    onError: (e) => setErr(apiError(e)),
  });

  const transition = useMutation({
    mutationFn: async (arg: { id: string; status: string }) =>
      (await api.post(`/schedule/assignments/${arg.id}/transition`, { status: arg.status })).data,
    onSuccess: () => {
      setErr(null);
      qc.invalidateQueries({ queryKey: ["assignments"] });
    },
    onError: (e) => setErr(apiError(e)),
  });

  const projectName = (id: string) => {
    const p = projects.data?.find((x) => x.id === id);
    return p ? p.code : "";
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Schedule</h1>
          <p className="text-sm text-slate-500">
            {canPlan
              ? "Who works on what, day by day. Double-bookings are warned about, never blocked."
              : "Your assignments. Confirm when you accept the plan; mark done when the work is."}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" onClick={() => setWeekStart(new Date(weekStart.getTime() - 7 * DAY_MS))}>
            ← Prev
          </Button>
          <Button variant="secondary" onClick={() => setWeekStart(startOfWeek(new Date()))}>
            Today
          </Button>
          <Button variant="secondary" onClick={() => setWeekStart(new Date(weekStart.getTime() + 7 * DAY_MS))}>
            Next →
          </Button>
        </div>
      </div>

      {err && (
        <div className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {err}
        </div>
      )}
      {overlapNote && (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          {overlapNote}
        </div>
      )}

      {canPlan && (
        <div className="flex flex-wrap gap-2">
          <select className="input" value={filterMember} onChange={(e) => setFilterMember(e.target.value)}>
            <option value="">Everyone</option>
            {(members.data ?? []).map((m) => (
              <option key={m.user_id} value={m.user_id}>
                {m.name || m.email}
              </option>
            ))}
          </select>
          <select className="input" value={filterProject} onChange={(e) => setFilterProject(e.target.value)}>
            <option value="">All projects</option>
            {(projects.data ?? []).map((p) => (
              <option key={p.id} value={p.id}>
                {p.code} · {p.name}
              </option>
            ))}
          </select>
          <Button
            onClick={() =>
              setForm({
                project_id: projects.data?.[0]?.id ?? "",
                assignee_user_id: members.data?.[0]?.user_id ?? "",
                date: weekStart.toISOString().slice(0, 10),
                start: "09:00",
                end: "17:00",
                all_day: false,
                note: "",
              })
            }
          >
            + Assign
          </Button>
        </div>
      )}

      {form && (
        <div className="card space-y-3 p-6">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            New assignment
          </h2>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <div>
              <label className="label">Project</label>
              <select
                className="input"
                value={form.project_id}
                onChange={(e) => setForm({ ...form, project_id: e.target.value })}
              >
                {(projects.data ?? []).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.code} · {p.name}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="label">Person</label>
              <select
                className="input"
                value={form.assignee_user_id}
                onChange={(e) => setForm({ ...form, assignee_user_id: e.target.value })}
              >
                {(members.data ?? []).map((m) => (
                  <option key={m.user_id} value={m.user_id}>
                    {m.name || m.email}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="label">Date</label>
              <input
                type="date"
                className="input"
                value={form.date}
                onChange={(e) => setForm({ ...form, date: e.target.value })}
              />
            </div>
            <div>
              <label className="label">
                <input
                  type="checkbox"
                  className="mr-2"
                  checked={form.all_day}
                  onChange={(e) => setForm({ ...form, all_day: e.target.checked })}
                />
                All day
              </label>
            </div>
            {!form.all_day && (
              <>
                <div>
                  <label className="label">From</label>
                  <input
                    type="time"
                    className="input"
                    value={form.start}
                    onChange={(e) => setForm({ ...form, start: e.target.value })}
                  />
                </div>
                <div>
                  <label className="label">To</label>
                  <input
                    type="time"
                    className="input"
                    value={form.end}
                    onChange={(e) => setForm({ ...form, end: e.target.value })}
                  />
                </div>
              </>
            )}
          </div>
          <div>
            <label className="label">Note</label>
            <input
              className="input"
              value={form.note}
              placeholder="Bring the signed contract"
              onChange={(e) => setForm({ ...form, note: e.target.value })}
            />
          </div>
          <div className="flex gap-2">
            <Button
              onClick={() => save.mutate()}
              disabled={save.isPending || !form.project_id || !form.assignee_user_id}
            >
              Save assignment
            </Button>
            <Button variant="secondary" onClick={() => setForm(null)}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      <PhoneSync />

      <QueryState
        query={assignments}
        loading={<Skeleton className="h-64 w-full" />}
        errorTitle="Couldn’t load the schedule"
      >
        {(rows) => (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-7">
            {days.map((day) => {
              const dayEnd = new Date(day.getTime() + DAY_MS);
              const todays = rows.filter(
                (a) => new Date(a.starts_at) < dayEnd && new Date(a.ends_at) > day,
              );
              return (
                <div key={day.toISOString()} className="card space-y-2 p-3">
                  <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                    {fmtDay(day)}
                  </div>
                  {todays.length === 0 ? (
                    <div className="text-xs text-slate-300">—</div>
                  ) : (
                    todays.map((a) => (
                      <div key={a.id} className="rounded-md border border-slate-200 p-2 text-xs">
                        <div className="flex items-center justify-between gap-1">
                          <span className="font-medium text-slate-700">
                            {projectName(a.project_id) || a.assignee_email.split("@")[0]}
                          </span>
                          <Badge tone={STATUS_TONE[a.status] ?? "neutral"}>{a.status}</Badge>
                        </div>
                        <div className="text-slate-500">
                          {a.all_day ? "All day" : `${fmtTime(a.starts_at)}–${fmtTime(a.ends_at)}`}
                          {" · "}
                          {a.assignee_email}
                        </div>
                        {a.note && <div className="text-slate-400">{a.note}</div>}
                        <div className="mt-1 flex gap-2">
                          {a.status === "planned" && a.assignee_user_id === user?.id && (
                            <button
                              className="btn-ghost text-xs"
                              onClick={() => transition.mutate({ id: a.id, status: "confirmed" })}
                            >
                              Confirm
                            </button>
                          )}
                          {["planned", "confirmed"].includes(a.status) &&
                            a.assignee_user_id === user?.id && (
                              <button
                                className="btn-ghost text-xs"
                                onClick={() => transition.mutate({ id: a.id, status: "done" })}
                              >
                                Mark done
                              </button>
                            )}
                          {canPlan && ["planned", "confirmed"].includes(a.status) && (
                            <button
                              className="btn-ghost text-xs text-rose-500"
                              onClick={() => transition.mutate({ id: a.id, status: "cancelled" })}
                            >
                              Cancel
                            </button>
                          )}
                        </div>
                      </div>
                    ))
                  )}
                </div>
              );
            })}
          </div>
        )}
      </QueryState>
    </div>
  );
}
