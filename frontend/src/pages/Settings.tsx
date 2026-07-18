import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { api, apiError } from "../lib/api";
import type { ModuleInfo, ValidationSettings } from "../lib/types";

export default function Settings() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const isOwner = user?.role === "owner";

  const settings = useQuery<ValidationSettings>({
    queryKey: ["settings", "validation"],
    queryFn: async () => (await api.get("/settings/validation")).data,
  });
  const modules = useQuery<ModuleInfo[]>({
    queryKey: ["modules"],
    queryFn: async () => (await api.get("/modules")).data,
  });

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

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="text-sm text-slate-500">
          Data validation for every invoice you save. Both options are off by default — turn on
          the ones you want.
        </p>
      </div>

      {!isOwner && (
        <div className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-700">
          Only the workspace owner can change these settings.
        </div>
      )}
      {update.isError && (
        <div className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-600">{apiError(update.error)}</div>
      )}

      <div className="card space-y-1">
        <h2 className="text-sm font-semibold text-slate-600">Invoice validation</h2>
        <Toggle
          title="AI validation"
          desc="Automatically check each saved invoice — totals, tax, duplicates, dates, FX vs ECB — and flag anything that looks wrong. Advisory; it never blocks a save."
          checked={!!s?.ai_validation_enabled}
          disabled={!isOwner || update.isPending}
          onChange={(v) => update.mutate({ ai_validation_enabled: v })}
        />
        <div className="border-t border-slate-100" />
        <Toggle
          title="Human validation"
          desc="Route every saved invoice to a review queue so a person approves or rejects it before it's considered validated."
          checked={!!s?.human_validation_enabled}
          disabled={!isOwner || update.isPending}
          onChange={(v) => update.mutate({ human_validation_enabled: v })}
        />
      </div>

      <p className="text-xs text-slate-400">
        With both on, AI findings are attached to help the reviewer, and the invoice still waits
        for a human. With neither on, invoices save straight through (unvalidated).
      </p>

      <div className="card space-y-1">
        <h2 className="text-sm font-semibold text-slate-600">Modules</h2>
        <p className="mb-2 text-sm text-slate-500">Activate the capabilities your workspace needs.</p>
        {(modules.data ?? []).filter((m) => !m.core).map((m) => (
          <div key={m.key}>
            <Toggle
              title={m.name}
              desc={m.description}
              checked={m.enabled}
              disabled={!isOwner || toggleModule.isPending}
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
        <div className="border-t border-slate-100 pt-2 text-xs text-slate-400">
          Core modules (analytics, intake, FX, validation) are always on.
        </div>
      </div>
    </div>
  );
}

function Toggle({
  title,
  desc,
  checked,
  disabled,
  onChange,
}: {
  title: string;
  desc: string;
  checked: boolean;
  disabled: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-start justify-between gap-4 py-3">
      <div>
        <div className="font-medium text-slate-700">{title}</div>
        <div className="text-sm text-slate-500">{desc}</div>
      </div>
      <button
        role="switch"
        aria-checked={checked}
        aria-label={title}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={`relative mt-1 h-6 w-11 shrink-0 rounded-full transition ${
          checked ? "bg-brand-500" : "bg-slate-300"
        } ${disabled ? "opacity-50" : ""}`}
      >
        <span
          className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all ${
            checked ? "left-[22px]" : "left-0.5"
          }`}
        />
      </button>
    </div>
  );
}
