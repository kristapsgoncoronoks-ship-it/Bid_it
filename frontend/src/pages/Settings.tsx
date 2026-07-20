import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { SettingRow } from "../components/SettingRow";
import { useToast } from "../components/Toast";
import { api, apiError } from "../lib/api";
import { isAdminOrAbove } from "../lib/roles";
import { useModules } from "../lib/useModules";
import type { ValidationSettings } from "../lib/types";

export default function Settings() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const canEdit = isAdminOrAbove(user);

  const settings = useQuery<ValidationSettings>({
    queryKey: ["settings", "validation"],
    queryFn: async () => (await api.get("/settings/validation")).data,
  });
  const modules = useModules();

  const update = useMutation({
    mutationFn: async (patch: Partial<ValidationSettings>) =>
      (await api.put("/settings/validation", patch)).data,
    onSuccess: (data) => qc.setQueryData(["settings", "validation"], data),
  });
  const toggleModule = useMutation({
    mutationFn: async (v: { key: string; enabled: boolean }) =>
      (await api.put(`/modules/${v.key}`, { enabled: v.enabled })).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["modules"] }),
    onError: (e) => toast.error(apiError(e)),
  });

  const s = settings.data;
  const addons = (modules.data ?? []).filter((m) => !m.core);

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Admin panel</h1>
        <p className="text-sm text-slate-500">
          Manage your workspace — turn capabilities on or off. Changes apply immediately.
        </p>
      </div>

      {!canEdit && (
        <div className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-700">
          You can view these settings, but only an admin can change them.
        </div>
      )}
      {update.isError && (
        <div className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-600">{apiError(update.error)}</div>
      )}

      {/* Modules */}
      <section className="space-y-2">
        <div className="px-1">
          <h2 className="text-sm font-semibold text-slate-600">Modules</h2>
          <p className="text-sm text-slate-500">Activate the capabilities your workspace needs.</p>
        </div>
        <div className="card divide-y divide-slate-100 py-1">
          {addons.map((m) => (
            <div key={m.key}>
              <SettingRow
                title={m.name}
                desc={m.description}
                checked={m.enabled}
                disabled={!canEdit || toggleModule.isPending}
                onChange={(v) => toggleModule.mutate({ key: m.key, enabled: v })}
              />
              {m.enabled && m.requires_issuer && !m.ready && (
                <div className="mb-3 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-700">
                  Activated — now{" "}
                  <Link to="/issuer" className="font-medium underline">complete your company registration details</Link>{" "}
                  to start issuing invoices.
                </div>
              )}
            </div>
          ))}
          {addons.length === 0 && <div className="py-3 text-sm text-slate-400">No optional modules.</div>}
        </div>
        <p className="px-1 text-xs text-slate-400">
          Core modules (analytics, intake, FX, validation) are always on.
        </p>
      </section>

      {/* Invoice validation */}
      <section className="space-y-2">
        <div className="px-1">
          <h2 className="text-sm font-semibold text-slate-600">Invoice validation</h2>
          <p className="text-sm text-slate-500">Checks run on every invoice you save. Both are off by default.</p>
        </div>
        <div className="card divide-y divide-slate-100 py-1">
          <SettingRow
            title="AI validation"
            desc="Automatically check each saved invoice — totals, tax, duplicates, dates, FX vs ECB — and flag anything that looks wrong. Advisory; it never blocks a save."
            checked={!!s?.ai_validation_enabled}
            disabled={!canEdit || update.isPending}
            onChange={(v) => update.mutate({ ai_validation_enabled: v })}
          />
          <SettingRow
            title="Human validation"
            desc="Route every saved invoice to a review queue so a person approves or rejects it before it's considered validated."
            checked={!!s?.human_validation_enabled}
            disabled={!canEdit || update.isPending}
            onChange={(v) => update.mutate({ human_validation_enabled: v })}
          />
        </div>
        <p className="px-1 text-xs text-slate-400">
          With both on, AI findings are attached to help the reviewer, and the invoice still waits for a human.
          With neither on, invoices save straight through (unvalidated).
        </p>
      </section>

      {canEdit && <BackgroundJobs />}
    </div>
  );
}

// Admin view over the durable background-job queue: enqueue the periodic jobs
// on demand, see recent runs, and retry a dead one. A worker drains the queue.
const JOB_STATUS_STYLES: Record<string, string> = {
  queued: "bg-slate-100 text-slate-600",
  running: "bg-sky-100 text-sky-700",
  succeeded: "bg-emerald-100 text-emerald-700",
  failed: "bg-amber-100 text-amber-700",
  dead: "bg-rose-100 text-rose-700",
};

interface JobRow {
  id: string; kind: string; status: string; attempts: number; max_attempts: number;
  last_error: string | null; result_json: string | null; created_at: string;
}

function BackgroundJobs() {
  const qc = useQueryClient();
  const toast = useToast();
  const jobs = useQuery<JobRow[]>({
    queryKey: ["jobs"],
    queryFn: async () => (await api.get("/jobs?limit=25")).data,
    refetchInterval: 4000,
  });

  const enqueue = useMutation({
    mutationFn: async (kind: string) => (await api.post("/jobs", { kind })).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
    onError: (e) => toast.error(apiError(e)),
  });
  const retry = useMutation({
    mutationFn: async (id: string) => (await api.post(`/jobs/${id}/retry`)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
    onError: (e) => toast.error(apiError(e)),
  });

  return (
    <section className="space-y-2">
      <div className="px-1">
        <h2 className="text-sm font-semibold text-slate-600">Background jobs</h2>
        <p className="text-sm text-slate-500">
          Durable, retrying queue drained by the worker. Recurring-invoice generation and overdue
          reminders run here automatically each day; you can also trigger them now.
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        <button className="btn-ghost" disabled={enqueue.isPending} onClick={() => enqueue.mutate("recurring.generate")}>
          Generate recurring now
        </button>
        <button className="btn-ghost" disabled={enqueue.isPending} onClick={() => enqueue.mutate("dunning.run")}>
          Send overdue reminders now
        </button>
      </div>
      <div className="card overflow-x-auto p-0">
        <table className="w-full text-sm">
          <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-2">Job</th>
              <th className="px-4 py-2">Status</th>
              <th className="px-4 py-2">Attempts</th>
              <th className="px-4 py-2">Result / error</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {jobs.data?.map((j) => (
              <tr key={j.id} className="border-b border-slate-100 last:border-0">
                <td className="px-4 py-2 font-mono text-xs">{j.kind}</td>
                <td className="px-4 py-2">
                  <span className={`badge ${JOB_STATUS_STYLES[j.status] ?? "bg-slate-100 text-slate-600"}`}>{j.status}</span>
                </td>
                <td className="px-4 py-2 text-slate-500">{j.attempts}/{j.max_attempts}</td>
                <td className="px-4 py-2 text-xs text-slate-400 max-w-xs truncate">
                  {j.status === "dead" || j.status === "failed" ? (j.last_error ?? "") : (j.result_json ?? "")}
                </td>
                <td className="px-4 py-2 text-right">
                  {(j.status === "dead" || j.status === "failed") && (
                    <button className="text-brand-600 hover:underline" disabled={retry.isPending} onClick={() => retry.mutate(j.id)}>
                      retry
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {jobs.data && jobs.data.length === 0 && (
              <tr><td colSpan={5} className="px-4 py-6 text-center text-slate-400">No background jobs yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
