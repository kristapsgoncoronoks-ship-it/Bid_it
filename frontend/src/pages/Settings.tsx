import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { SettingRow } from "../components/SettingRow";
import { api, apiError } from "../lib/api";
import { isAdminOrAbove } from "../lib/roles";
import { useModules } from "../lib/useModules";
import type { ValidationSettings } from "../lib/types";

export default function Settings() {
  const { user } = useAuth();
  const qc = useQueryClient();
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
    </div>
  );
}
