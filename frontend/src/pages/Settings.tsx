import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { SettingRow } from "../components/SettingRow";
import { useToast } from "../components/Toast";
import { api, apiError } from "../lib/api";
import { isAdminOrAbove } from "../lib/roles";
import { useModules } from "../lib/useModules";
import type {
  ErasureReport, IntegrityReport, RetentionInfo, SsoConnection, ValidationSettings,
  WebhookCreated, WebhookDelivery, WebhookEndpoint,
} from "../lib/types";

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
      {canEdit && <Webhooks />}
      {canEdit && <IntegrityCheck />}
      {canEdit && <RetentionPanel />}
      {canEdit && <ErasurePanel />}
      {canEdit && <SsoPanel />}
    </div>
  );
}

// Admin: OIDC single-sign-on connection (ADR-0021). The client secret is
// write-only (never returned); SAML is scaffolded but not yet selectable here.
function SsoPanel() {
  const qc = useQueryClient();
  const toast = useToast();
  const conn = useQuery<SsoConnection | null>({
    queryKey: ["sso"],
    queryFn: async () => (await api.get("/sso/connection")).data,
  });

  const [form, setForm] = useState<Record<string, string | boolean>>({});
  const [tokenOnce, setTokenOnce] = useState<string | null>(null);
  const c = conn.data;

  const scimToken = useMutation({
    mutationFn: async () => (await api.post("/sso/scim/token")).data as { token: string },
    onSuccess: (d) => { setTokenOnce(d.token); qc.invalidateQueries({ queryKey: ["sso"] }); },
    onError: (e) => toast.error(apiError(e)),
  });
  const val = (k: string, dflt: string | boolean = "") =>
    (k in form ? form[k] : (c as any)?.[k] ?? dflt);

  const save = useMutation({
    mutationFn: async () => (await api.put("/sso/connection", form)).data as SsoConnection,
    onSuccess: (d) => { qc.setQueryData(["sso"], d); setForm({}); toast.success("SSO connection saved."); },
    onError: (e) => toast.error(apiError(e)),
  });

  const field = (k: string, label: string, type = "text", placeholder = "") => (
    <div>
      <label className="label">{label}</label>
      <input
        className="input" type={type} placeholder={placeholder}
        value={String(val(k) ?? "")}
        onChange={(e) => setForm((p) => ({ ...p, [k]: e.target.value }))}
      />
    </div>
  );

  return (
    <section className="space-y-2">
      <div className="px-1">
        <h2 className="text-sm font-semibold text-slate-600">Single sign-on (OIDC)</h2>
        <p className="text-sm text-slate-500">
          Let your team sign in with your identity provider (Okta, Entra, Google, Keycloak). Users are
          provisioned on first login. The client secret is stored write-only and never shown again.
        </p>
      </div>
      <div className="card grid grid-cols-1 gap-3 md:grid-cols-2">
        {field("slug", "Workspace ID (login URL)", "text", "acme")}
        {field("issuer", "OIDC issuer URL", "text", "https://idp.example.com")}
        {field("client_id", "Client ID")}
        {field("client_secret", c?.has_client_secret ? "Client secret (set — leave blank to keep)" : "Client secret", "password")}
        {field("allowed_domain", "Restrict to email domain (optional)", "text", "example.com")}
        <div>
          <label className="label">Default role for new users</label>
          <select className="input" value={String(val("default_role", "user"))}
                  onChange={(e) => setForm((p) => ({ ...p, default_role: e.target.value }))}>
            <option value="user">user</option>
            <option value="processor">processor</option>
            <option value="admin">admin</option>
          </select>
        </div>
        <label className="flex items-center gap-2 text-sm text-slate-600">
          <input type="checkbox" checked={Boolean(val("enabled", false))}
                 onChange={(e) => setForm((p) => ({ ...p, enabled: e.target.checked }))} />
          Enabled
        </label>
        <label className="flex items-center gap-2 text-sm text-slate-600">
          <input type="checkbox" checked={Boolean(val("jit_enabled", true))}
                 onChange={(e) => setForm((p) => ({ ...p, jit_enabled: e.target.checked }))} />
          Just-in-time provisioning
        </label>
        <div className="md:col-span-2 flex items-center justify-between">
          {c?.login_url
            ? <span className="text-xs text-slate-400">Login URL: <code>{c.login_url}</code></span>
            : <span />}
          <button className="btn-primary" disabled={save.isPending} onClick={() => save.mutate()}>
            Save connection
          </button>
        </div>

        {c?.id && (
          <div className="md:col-span-2 border-t border-slate-100 pt-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="text-sm">
                <span className="font-medium text-slate-700">SCIM provisioning</span>
                <span className="ml-2 text-xs text-slate-400">
                  {c.scim_enabled ? "enabled" : "off"} · your IdP creates/deactivates users automatically
                </span>
              </div>
              <button
                className="btn-ghost"
                disabled={scimToken.isPending}
                onClick={() => scimToken.mutate()}
              >
                {c.scim_enabled ? "Regenerate SCIM token" : "Enable SCIM & generate token"}
              </button>
            </div>
            {tokenOnce && (
              <div className="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
                Copy this token now — it is shown only once. Base URL: <code>{c.scim_base_url}</code>
                <div className="mt-1 break-all font-mono text-slate-700">{tokenOnce}</div>
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

// Admin: GDPR right-to-erasure (DSAR). Preview classifies every personal-data
// location; erase performs the erasable ones and reports what is retained
// (statutory / integrity) or blocked by a legal hold.
function ErasurePanel() {
  const toast = useToast();
  const [email, setEmail] = useState("");
  const [report, setReport] = useState<ErasureReport | null>(null);

  const preview = useMutation({
    mutationFn: async () => (await api.post("/privacy/erasure/preview", { email })).data as ErasureReport,
    onSuccess: setReport,
    onError: (e) => toast.error(apiError(e)),
  });
  const erase = useMutation({
    mutationFn: async () => (await api.post("/privacy/erasure", { email })).data as ErasureReport,
    onSuccess: (r) => {
      setReport(r);
      toast.success(r.executed ? "Erasure completed." : "Erasure blocked by an active legal hold.");
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const badge = (a: string) =>
    a === "erase" ? "bg-rose-100 text-rose-700"
    : a === "retain" ? "bg-slate-100 text-slate-600"
    : "bg-amber-100 text-amber-700";

  return (
    <section className="space-y-2">
      <div className="px-1">
        <h2 className="text-sm font-semibold text-slate-600">Right to erasure (GDPR)</h2>
        <p className="text-sm text-slate-500">
          Erase a person's personal data by email. Statutory records (issued tax invoices) and the
          tamper-evident audit trail are kept by law and reported, not deleted; a legal hold suspends erasure.
        </p>
      </div>
      <div className="card space-y-3">
        <div className="flex items-center gap-2">
          <input
            className="input flex-1"
            type="email"
            placeholder="person@example.com"
            value={email}
            onChange={(e) => { setEmail(e.target.value); setReport(null); }}
          />
          <button className="btn-ghost" disabled={!email.trim() || preview.isPending} onClick={() => preview.mutate()}>
            Preview
          </button>
          <button
            className="btn-primary"
            disabled={!report || report.executed || preview.isPending || erase.isPending}
            onClick={() => { if (window.confirm(`Permanently erase personal data for ${email}? This cannot be undone.`)) erase.mutate(); }}
          >
            Erase
          </button>
        </div>

        {report?.on_hold && (
          <div className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-700">
            A legal hold is active — erasure is suspended until it is released.
          </div>
        )}

        {report && (
          <div className="space-y-1">
            {report.locations.map((l) => (
              <div key={l.key} className="flex items-start justify-between gap-3 border-b border-slate-100 py-1.5 text-sm last:border-0">
                <div>
                  <span className="font-medium text-slate-700">{l.label}</span>
                  <span className="ml-2 text-xs text-slate-400">{l.matched} match{l.matched === 1 ? "" : "es"}</span>
                  {l.reason && <div className="text-xs text-slate-400">{l.reason}</div>}
                </div>
                <span className={`badge ${badge(l.action)}`}>{l.action}</span>
              </div>
            ))}
            {report.executed && (
              <div className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-700">Erasure completed and audited.</div>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

// Admin: data-retention windows per category + legal hold. Retention is opt-in
// (blank = keep forever); a legal hold suspends all purging.
function RetentionPanel() {
  const qc = useQueryClient();
  const toast = useToast();
  const [reason, setReason] = useState("");
  const [drafts, setDrafts] = useState<Record<string, string>>({});

  const info = useQuery<RetentionInfo>({
    queryKey: ["retention"],
    queryFn: async () => (await api.get("/retention")).data,
  });

  const setPolicy = useMutation({
    mutationFn: async (v: { category: string; retain_days: number }) =>
      (await api.put("/retention/policy", v)).data as RetentionInfo,
    onSuccess: (d) => qc.setQueryData(["retention"], d),
    onError: (e) => toast.error(apiError(e)),
  });
  const placeHold = useMutation({
    mutationFn: async () => (await api.post("/retention/holds", { reason })).data,
    onSuccess: () => { setReason(""); qc.invalidateQueries({ queryKey: ["retention"] }); },
    onError: (e) => toast.error(apiError(e)),
  });
  const releaseHold = useMutation({
    mutationFn: async (id: string) => (await api.post(`/retention/holds/${id}/release`)).data as RetentionInfo,
    onSuccess: (d) => qc.setQueryData(["retention"], d),
    onError: (e) => toast.error(apiError(e)),
  });
  const purge = useMutation({
    mutationFn: async () => (await api.post("/retention/purge")).data as { held: boolean; purged: Record<string, number> },
    onSuccess: (r) => {
      const total = Object.values(r.purged).reduce((a, b) => a + b, 0);
      toast.success(r.held ? "Purge blocked by an active legal hold." : `Purge complete — ${total} record(s) removed.`);
      qc.invalidateQueries({ queryKey: ["retention"] });
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const d = info.data;
  const busy = setPolicy.isPending || placeHold.isPending || releaseHold.isPending || purge.isPending;

  return (
    <section className="space-y-2">
      <div className="px-1">
        <h2 className="text-sm font-semibold text-slate-600">Data retention &amp; legal hold</h2>
        <p className="text-sm text-slate-500">
          Automatically delete records older than a set number of days (measured from when they entered the
          system). Leave blank to keep forever. A legal hold suspends all deletion.
        </p>
      </div>

      {d?.on_hold && (
        <div className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-700">
          A legal hold is active — automatic and manual purges are suspended until it is released.
        </div>
      )}

      <div className="card space-y-3">
        {d?.categories.map((c) => (
          <div key={c.key} className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 pb-2 last:border-0 last:pb-0">
            <div>
              <div className="text-sm font-medium text-slate-700">{c.label}</div>
              <div className="text-xs text-slate-400">
                {c.retain_days ? `Keep ${c.retain_days} days` : "Kept forever"}
                {c.retain_days ? ` · ${c.purgeable_now} record(s) purgeable now` : ""}
              </div>
            </div>
            <div className="flex items-center gap-2">
              <input
                type="number"
                min={0}
                className="input w-28"
                placeholder="days"
                value={drafts[c.key] ?? (c.retain_days ?? "")}
                onChange={(e) => setDrafts((p) => ({ ...p, [c.key]: e.target.value }))}
              />
              <button
                className="btn-ghost py-1"
                disabled={busy}
                onClick={() => setPolicy.mutate({ category: c.key, retain_days: Number(drafts[c.key] ?? c.retain_days ?? 0) })}
              >
                Save
              </button>
            </div>
          </div>
        ))}
        <button className="btn-ghost" disabled={busy || d?.on_hold} onClick={() => purge.mutate()}>
          Run purge now
        </button>
      </div>

      <div className="card space-y-3">
        <div className="text-sm font-medium text-slate-700">Legal holds</div>
        {d && d.holds.length === 0 && <p className="text-xs text-slate-400">No active holds.</p>}
        {d?.holds.map((h) => (
          <div key={h.id} className="flex items-center justify-between gap-2 text-sm">
            <div>
              <span className="badge bg-amber-100 text-amber-700">active</span>{" "}
              <span className="text-slate-600">{h.reason}</span>
            </div>
            <button className="btn-ghost py-1" disabled={busy} onClick={() => releaseHold.mutate(h.id)}>
              Release
            </button>
          </div>
        ))}
        <div className="flex items-center gap-2">
          <input
            className="input flex-1"
            placeholder="Reason for the hold (e.g. litigation ref)"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          <button className="btn-primary py-1" disabled={busy || !reason.trim()} onClick={() => placeHold.mutate()}>
            Place hold
          </button>
        </div>
      </div>
    </section>
  );
}

// Admin: re-hash stored documents (receipts, logos, email attachments) against
// their recorded sha256 to detect corruption or loss.
function IntegrityCheck() {
  const toast = useToast();
  const verify = useMutation({
    mutationFn: async () => (await api.post("/integrity/documents/verify")).data as IntegrityReport,
    onError: (e) => toast.error(apiError(e)),
  });
  const r = verify.data;

  return (
    <section className="space-y-2">
      <div className="px-1">
        <h2 className="text-sm font-semibold text-slate-600">Data integrity</h2>
        <p className="text-sm text-slate-500">
          Re-hash every stored document against its recorded checksum to detect silent corruption or a lost file.
        </p>
      </div>
      <div className="card space-y-3">
        <button className="btn-ghost" disabled={verify.isPending} onClick={() => verify.mutate()}>
          {verify.isPending ? "Verifying…" : "Verify stored documents"}
        </button>
        {r && (
          <div className="space-y-2">
            <div className={`rounded-lg px-3 py-2 text-sm ${r.healthy ? "bg-emerald-50 text-emerald-700" : "bg-rose-50 text-rose-700"}`}>
              {r.healthy
                ? `All ${r.ok} document${r.ok === 1 ? "" : "s"} verified — no corruption or loss.`
                : `${r.issues.length} problem${r.issues.length === 1 ? "" : "s"} found across ${r.checked} document${r.checked === 1 ? "" : "s"}.`}
            </div>
            {r.issues.length > 0 && (
              <ul className="space-y-1 text-xs">
                {r.issues.map((i, idx) => (
                  <li key={idx} className="flex items-center gap-2 rounded bg-slate-50 px-2 py-1">
                    <span className="badge bg-rose-100 text-rose-700">{i.problem}</span>
                    <span className="font-mono text-slate-600">{i.kind}</span>
                    <span className="text-slate-400">{i.detail}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

const WEBHOOK_STATUS_STYLES: Record<string, string> = {
  pending: "bg-slate-100 text-slate-600",
  delivered: "bg-emerald-100 text-emerald-700",
  failed: "bg-rose-100 text-rose-700",
};

// Admin surface for outbound webhooks: register signed HTTP endpoints, send a
// test ping, and see recent delivery outcomes. The signing secret is shown once.
function Webhooks() {
  const qc = useQueryClient();
  const toast = useToast();
  const [url, setUrl] = useState("");
  const [events, setEvents] = useState("*");
  const [secret, setSecret] = useState<string | null>(null);

  const endpoints = useQuery<WebhookEndpoint[]>({
    queryKey: ["webhooks"],
    queryFn: async () => (await api.get("/webhooks")).data,
  });

  const create = useMutation({
    mutationFn: async () => (await api.post("/webhooks", { url, events })).data as WebhookCreated,
    onSuccess: (w) => {
      setSecret(w.secret);
      setUrl("");
      qc.invalidateQueries({ queryKey: ["webhooks"] });
    },
    onError: (e) => toast.error(apiError(e)),
  });
  const ping = useMutation({
    mutationFn: async (id: string) => (await api.post(`/webhooks/${id}/ping`)).data,
    onSuccess: () => toast.success("Test event enqueued"),
    onError: (e) => toast.error(apiError(e)),
  });
  const remove = useMutation({
    mutationFn: async (id: string) => api.delete(`/webhooks/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["webhooks"] }),
  });

  return (
    <section className="space-y-2">
      <div className="px-1">
        <h2 className="text-sm font-semibold text-slate-600">Webhooks</h2>
        <p className="text-sm text-slate-500">
          POST events (invoice created, payment recorded, expense approved…) to your systems.
          Each request is signed with the endpoint's secret (header <code>X-InvoiceIQ-Signature</code>).
        </p>
      </div>

      <div className="card space-y-3">
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex-1">
            <label className="label" htmlFor="wh-url">Endpoint URL</label>
            <input id="wh-url" className="input" placeholder="https://your-app.example/webhooks/invoiceiq"
              value={url} onChange={(e) => setUrl(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="wh-events">Events</label>
            <input id="wh-events" className="input w-40" value={events} onChange={(e) => setEvents(e.target.value)} />
          </div>
          <button className="btn-primary" disabled={create.isPending || !url} onClick={() => create.mutate()}>
            {create.isPending ? "Adding…" : "Add endpoint"}
          </button>
        </div>
        {secret && (
          <div className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
            Signing secret (shown once — store it now): <code className="font-mono">{secret}</code>
          </div>
        )}

        {(endpoints.data?.length ?? 0) > 0 && (
          <div className="divide-y divide-slate-100">
            {endpoints.data!.map((ep) => (
              <WebhookRow key={ep.id} ep={ep}
                onPing={() => ping.mutate(ep.id)}
                onDelete={() => { if (window.confirm("Delete this webhook endpoint?")) remove.mutate(ep.id); }} />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

function WebhookRow({ ep, onPing, onDelete }: { ep: WebhookEndpoint; onPing: () => void; onDelete: () => void }) {
  const [open, setOpen] = useState(false);
  const deliveries = useQuery<WebhookDelivery[]>({
    queryKey: ["webhooks", ep.id, "deliveries"],
    queryFn: async () => (await api.get(`/webhooks/${ep.id}/deliveries?limit=10`)).data,
    enabled: open,
    refetchInterval: open ? 4000 : false,
  });

  return (
    <div className="py-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className={`badge ${ep.active ? "bg-emerald-100 text-emerald-700" : "bg-slate-100 text-slate-500"}`}>
          {ep.active ? "active" : "paused"}
        </span>
        <span className="font-mono text-xs text-slate-600 truncate">{ep.url}</span>
        <span className="text-xs text-slate-400">· {ep.events}</span>
        <div className="ml-auto flex items-center gap-2">
          <button className="text-brand-600 hover:underline" onClick={() => setOpen((o) => !o)}>
            {open ? "hide" : "deliveries"}
          </button>
          <span className="text-slate-300">·</span>
          <button className="text-brand-600 hover:underline" onClick={onPing}>ping</button>
          <span className="text-slate-300">·</span>
          <button className="text-rose-500 hover:underline" onClick={onDelete}>delete</button>
        </div>
      </div>
      {open && (
        <div className="mt-2 space-y-1">
          {deliveries.data?.length ? deliveries.data.map((d) => (
            <div key={d.id} className="flex items-center gap-2 rounded bg-slate-50 px-2 py-1 text-xs">
              <span className={`badge ${WEBHOOK_STATUS_STYLES[d.status] ?? ""}`}>{d.status}</span>
              <span className="font-mono text-slate-600">{d.event_type}</span>
              <span className="text-slate-400">{d.response_code ?? "—"}</span>
              <span className="ml-auto text-slate-400">{d.attempts} attempt{d.attempts === 1 ? "" : "s"}</span>
            </div>
          )) : <p className="text-xs text-slate-400">No deliveries yet.</p>}
        </div>
      )}
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
