import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Badge, Button, Card } from "../components/ui";
import { api, apiError } from "../lib/api";

/** WO-J: the admin rule builder over the bounded trigger-condition-action
 * engine. The vocabulary (triggers, actions) comes from GET /automation/meta;
 * the condition composer only ever emits the backend's closed JSON-Logic
 * subset — a condition the rows can't express is edited as raw JSON instead
 * of being silently mangled. */

interface Rule {
  id: string;
  name: string;
  trigger: string;
  condition: unknown;
  actions: ActionDef[];
  status: "draft" | "published" | "disabled";
  fire_policy: string;
  cooldown_hours: number | null;
  published_version: number | null;
  created_at: string;
}

interface ActionDef {
  kind: string;
  subject?: string;
  body?: string;
}

interface Run {
  id: string;
  rule_id: string;
  version: number;
  ref_id: string;
  status: string;
  detail: unknown;
  at: string;
}

interface CondRow {
  field: string;
  op: string;
  value: string;
}

const TRIGGER_LABEL: Record<string, string> = {
  "offer.sent_stale": "Offer sent, gone quiet",
  "issued.overdue": "Invoice overdue",
  "project.accepted": "Work accepted",
  "assignment.done_all": "All visits done",
  "customer.dormant": "Customer dormant",
};

/** The context fields each trigger's matcher provides (mirrors the backend
 * matchers in services/automation.py — the template placeholders too). */
const TRIGGER_FIELDS: Record<string, string[]> = {
  "offer.sent_stale": ["days_quiet", "total", "offer_number", "offer_title"],
  "issued.overdue": ["days_overdue", "outstanding", "total", "invoice_number"],
  "project.accepted": ["days_since_accepted", "project_code", "project_name"],
  "assignment.done_all": ["done_count", "project_code", "project_name"],
  "customer.dormant": ["days_since_last_invoice", "lifecycle", "customer_name"],
};

const ACTION_LABEL: Record<string, string> = {
  notify_owner_email: "Email me (the owner)",
  notify_customer_email: "Email the customer",
  create_customer_note: "Add a CRM note",
};

const OPS = [">", ">=", "<", "<=", "==", "!=", "in"] as const;

const STATUS_TONE: Record<Rule["status"], "neutral" | "success" | "warning"> = {
  draft: "neutral",
  published: "success",
  disabled: "warning",
};

function rowsToCondition(rows: CondRow[]): unknown {
  const nodes = rows
    .filter((r) => r.field && r.value.trim() !== "")
    .map((r) => {
      let val: unknown = r.value.trim();
      if (r.op === "in") {
        val = r.value.split(",").map((v) => v.trim()).filter(Boolean);
      } else if (val !== "" && !Number.isNaN(Number(val))) {
        val = Number(val);
      }
      return { [r.op]: [{ var: r.field }, val] };
    });
  if (nodes.length === 0) return null;
  return nodes.length === 1 ? nodes[0] : { and: nodes };
}

/** Best-effort inverse of rowsToCondition; null when the condition uses shapes
 * the row composer can't express (nested or/!, var-vs-var …). */
function conditionToRows(cond: unknown): CondRow[] | null {
  if (cond == null) return [];
  const nodes: unknown[] =
    typeof cond === "object" && cond !== null && "and" in (cond as object)
      ? ((cond as Record<string, unknown>).and as unknown[])
      : [cond];
  if (!Array.isArray(nodes)) return null;
  const rows: CondRow[] = [];
  for (const n of nodes) {
    if (typeof n !== "object" || n === null) return null;
    const entries = Object.entries(n as Record<string, unknown>);
    if (entries.length !== 1) return null;
    const [op, args] = entries[0];
    if (!(OPS as readonly string[]).includes(op) || !Array.isArray(args) || args.length !== 2) {
      return null;
    }
    const [left, right] = args as [unknown, unknown];
    if (typeof left !== "object" || left === null || !("var" in (left as object))) return null;
    const field = String((left as Record<string, unknown>).var);
    if (op === "in") {
      if (!Array.isArray(right)) return null;
      rows.push({ field, op, value: right.join(", ") });
    } else if (typeof right === "string" || typeof right === "number") {
      rows.push({ field, op, value: String(right) });
    } else {
      return null;
    }
  }
  return rows;
}

interface Draft {
  name: string;
  trigger: string;
  rows: CondRow[];
  rawCondition: string; // used only when rows === null (advanced shape)
  advanced: boolean;
  actions: ActionDef[];
  fire_policy: string;
  cooldown_hours: string;
}

const EMPTY_DRAFT: Draft = {
  name: "",
  trigger: "offer.sent_stale",
  rows: [],
  rawCondition: "",
  advanced: false,
  actions: [{ kind: "notify_owner_email", subject: "", body: "" }],
  fire_policy: "once_per_record",
  cooldown_hours: "24",
};

function draftFromRule(r: Rule): Draft {
  const rows = conditionToRows(r.condition);
  return {
    name: r.name,
    trigger: r.trigger,
    rows: rows ?? [],
    rawCondition: rows === null ? JSON.stringify(r.condition, null, 2) : "",
    advanced: rows === null,
    actions: r.actions.map((a) => ({ ...a })),
    fire_policy: r.fire_policy,
    cooldown_hours: r.cooldown_hours != null ? String(r.cooldown_hours) : "24",
  };
}

const input = "rounded-lg border border-slate-300 px-2 py-1 text-sm";

export default function AutomationPage() {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null); // rule id; null = new
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [err, setErr] = useState<string | null>(null);
  const [dryRun, setDryRun] = useState<{ rule: string; outcomes: unknown[] } | null>(null);

  const meta = useQuery<{ triggers: Record<string, string>; actions: string[] }>({
    queryKey: ["automation-meta"],
    queryFn: async () => (await api.get("/automation/meta")).data,
  });
  const rules = useQuery<Rule[]>({
    queryKey: ["automation-rules"],
    queryFn: async () => (await api.get("/automation/rules")).data,
  });
  const runs = useQuery<Run[]>({
    queryKey: ["automation-runs"],
    queryFn: async () => (await api.get("/automation/runs?limit=50")).data,
  });

  // Shape-guard: an unexpected non-array response must degrade to an empty
  // list, never crash the page (same defence CustomerCard uses).
  const ruleList = useMemo(() => (Array.isArray(rules.data) ? rules.data : []), [rules.data]);
  const runList = useMemo(() => (Array.isArray(runs.data) ? runs.data : []), [runs.data]);

  const ruleName = useMemo(() => {
    const m = new Map<string, string>();
    for (const r of ruleList) m.set(r.id, r.name);
    return m;
  }, [ruleList]);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["automation-rules"] });
    qc.invalidateQueries({ queryKey: ["automation-runs"] });
  };

  const buildPayload = () => {
    let condition: unknown;
    if (draft.advanced) {
      condition = draft.rawCondition.trim() ? JSON.parse(draft.rawCondition) : null;
    } else {
      condition = rowsToCondition(draft.rows);
    }
    return {
      name: draft.name,
      trigger: draft.trigger,
      condition,
      actions: draft.actions,
      fire_policy: draft.fire_policy,
      cooldown_hours:
        draft.fire_policy === "cooldown" ? Number(draft.cooldown_hours) || 1 : null,
    };
  };

  const save = useMutation({
    mutationFn: async () => {
      const p = buildPayload();
      if (selected) {
        return (
          await api.patch(`/automation/rules/${selected}`, { ...p, set_condition: true })
        ).data as Rule;
      }
      return (await api.post("/automation/rules", p)).data as Rule;
    },
    onSuccess: (r) => {
      setErr(null);
      setSelected(r.id);
      setEditing(false);
      refresh();
    },
    onError: (e) => setErr(apiError(e)),
  });

  const publish = useMutation({
    mutationFn: async (id: string) =>
      (await api.post(`/automation/rules/${id}/publish`)).data as Rule,
    onSuccess: () => {
      setErr(null);
      refresh();
    },
    onError: (e) => setErr(apiError(e)),
  });

  const setStatus = useMutation({
    mutationFn: async (v: { id: string; status: string }) =>
      (await api.put(`/automation/rules/${v.id}/status`, { status: v.status })).data as Rule,
    onSuccess: () => {
      setErr(null);
      refresh();
    },
    onError: (e) => setErr(apiError(e)),
  });

  const revert = useMutation({
    mutationFn: async (v: { id: string; version: number }) =>
      (await api.post(`/automation/rules/${v.id}/revert/${v.version}`)).data as Rule,
    onSuccess: () => {
      setErr(null);
      refresh();
    },
    onError: (e) => setErr(apiError(e)),
  });

  const doDryRun = useMutation({
    mutationFn: async (id: string) =>
      (await api.post(`/automation/rules/${id}/dry-run`)).data as { outcomes: unknown[] },
    onSuccess: (data, id) => {
      setErr(null);
      setDryRun({ rule: ruleName.get(id) ?? id, outcomes: data.outcomes });
    },
    onError: (e) => setErr(apiError(e)),
  });

  const startNew = () => {
    setSelected(null);
    setDraft(EMPTY_DRAFT);
    setEditing(true);
    setDryRun(null);
  };
  const startEdit = (r: Rule) => {
    setSelected(r.id);
    setDraft(draftFromRule(r));
    setEditing(true);
    setDryRun(null);
  };

  const fields = TRIGGER_FIELDS[draft.trigger] ?? [];
  const updateRow = (i: number, patch: Partial<CondRow>) =>
    setDraft((d) => ({ ...d, rows: d.rows.map((r, j) => (j === i ? { ...r, ...patch } : r)) }));
  const updateAction = (i: number, patch: Partial<ActionDef>) =>
    setDraft((d) => ({
      ...d,
      actions: d.actions.map((a, j) => (j === i ? { ...a, ...patch } : a)),
    }));
  const moveAction = (i: number, dir: -1 | 1) =>
    setDraft((d) => {
      const next = [...d.actions];
      const j = i + dir;
      if (j < 0 || j >= next.length) return d;
      [next[i], next[j]] = [next[j], next[i]];
      return { ...d, actions: next };
    });

  const selectedRule = ruleList.find((r) => r.id === selected);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Automation</h1>
        <p className="text-slate-500">
          Rules that watch your work and act for you — chase quiet offers, nudge overdue
          invoices, note dormant customers. A rule runs only after you publish it; the run
          log below shows everything it did.
        </p>
      </div>

      {err && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-700">
          {err}
        </div>
      )}

      <Card>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="font-medium">Rules</h2>
          <Button size="sm" onClick={startNew}>
            New rule
          </Button>
        </div>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-slate-400">
              <th className="py-1">Name</th>
              <th className="py-1">When</th>
              <th className="py-1">Status</th>
              <th className="py-1">Version</th>
              <th className="py-1">Fires</th>
              <th className="py-1"></th>
            </tr>
          </thead>
          <tbody>
            {ruleList.map((r) => (
              <tr key={r.id} className="border-t border-slate-100">
                <td className="py-1.5 font-medium">{r.name}</td>
                <td className="py-1.5">{TRIGGER_LABEL[r.trigger] ?? r.trigger}</td>
                <td className="py-1.5">
                  <Badge tone={STATUS_TONE[r.status]}>{r.status}</Badge>
                </td>
                <td className="py-1.5">{r.published_version ? `v${r.published_version}` : "—"}</td>
                <td className="py-1.5 text-slate-500">
                  {r.fire_policy === "once_per_record"
                    ? "once per record"
                    : r.fire_policy === "cooldown"
                      ? `every ${r.cooldown_hours ?? "?"}h`
                      : "every sweep"}
                </td>
                <td className="py-1.5 text-right">
                  <div className="flex justify-end gap-1">
                    <Button size="sm" variant="ghost" onClick={() => startEdit(r)}>
                      Edit
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      loading={doDryRun.isPending}
                      onClick={() => doDryRun.mutate(r.id)}
                    >
                      Dry run
                    </Button>
                    {r.status === "draft" && (
                      <Button
                        size="sm"
                        variant="secondary"
                        loading={publish.isPending}
                        onClick={() => publish.mutate(r.id)}
                      >
                        Publish
                      </Button>
                    )}
                    {r.status === "published" && (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setStatus.mutate({ id: r.id, status: "disabled" })}
                      >
                        Disable
                      </Button>
                    )}
                    {r.status === "disabled" && r.published_version != null && (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setStatus.mutate({ id: r.id, status: "published" })}
                      >
                        Enable
                      </Button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
            {ruleList.length === 0 && (
              <tr>
                <td colSpan={6} className="py-3 text-slate-400">
                  No rules yet. Create one and publish it — the daily sweep does the rest.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Card>

      {editing && (
        <Card>
          <h2 className="mb-3 font-medium">{selected ? "Edit rule" : "New rule"}</h2>
          <div className="space-y-4">
            <div className="flex flex-wrap items-end gap-3">
              <label className="block text-sm">
                <span className="mb-1 block text-xs text-slate-500">Name</span>
                <input
                  className={`${input} w-64`}
                  value={draft.name}
                  onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                  placeholder="Chase quiet offers"
                />
              </label>
              <label className="block text-sm">
                <span className="mb-1 block text-xs text-slate-500">When</span>
                <select
                  className={input}
                  value={draft.trigger}
                  onChange={(e) => setDraft({ ...draft, trigger: e.target.value, rows: [] })}
                >
                  {Object.keys(meta.data?.triggers ?? TRIGGER_LABEL).map((t) => (
                    <option key={t} value={t}>
                      {TRIGGER_LABEL[t] ?? t}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block text-sm">
                <span className="mb-1 block text-xs text-slate-500">Fire policy</span>
                <select
                  className={input}
                  value={draft.fire_policy}
                  onChange={(e) => setDraft({ ...draft, fire_policy: e.target.value })}
                >
                  <option value="once_per_record">Once per record</option>
                  <option value="cooldown">With cooldown</option>
                  <option value="every_time">Every sweep</option>
                </select>
              </label>
              {draft.fire_policy === "cooldown" && (
                <label className="block text-sm">
                  <span className="mb-1 block text-xs text-slate-500">Cooldown (hours)</span>
                  <input
                    type="number"
                    min={1}
                    className={`${input} w-24`}
                    value={draft.cooldown_hours}
                    onChange={(e) => setDraft({ ...draft, cooldown_hours: e.target.value })}
                  />
                </label>
              )}
            </div>

            <div>
              <div className="mb-1 text-xs text-slate-500">
                Only when… (all lines must hold; leave empty to always act)
              </div>
              {draft.advanced ? (
                <textarea
                  className={`${input} h-32 w-full font-mono`}
                  value={draft.rawCondition}
                  onChange={(e) => setDraft({ ...draft, rawCondition: e.target.value })}
                  aria-label="Condition JSON"
                />
              ) : (
                <div className="space-y-2">
                  {draft.rows.map((r, i) => (
                    <div key={i} className="flex items-center gap-2">
                      <select
                        className={input}
                        value={r.field}
                        onChange={(e) => updateRow(i, { field: e.target.value })}
                        aria-label={`Condition ${i + 1} field`}
                      >
                        {fields.map((f) => (
                          <option key={f} value={f}>
                            {f.replace(/_/g, " ")}
                          </option>
                        ))}
                      </select>
                      <select
                        className={input}
                        value={r.op}
                        onChange={(e) => updateRow(i, { op: e.target.value })}
                        aria-label={`Condition ${i + 1} operator`}
                      >
                        {OPS.map((o) => (
                          <option key={o} value={o}>
                            {o === "in" ? "is one of" : o}
                          </option>
                        ))}
                      </select>
                      <input
                        className={`${input} w-40`}
                        value={r.value}
                        onChange={(e) => updateRow(i, { value: e.target.value })}
                        placeholder={r.op === "in" ? "a, b, c" : "value"}
                        aria-label={`Condition ${i + 1} value`}
                      />
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() =>
                          setDraft((d) => ({ ...d, rows: d.rows.filter((_, j) => j !== i) }))
                        }
                      >
                        Remove
                      </Button>
                    </div>
                  ))}
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() =>
                      setDraft((d) => ({
                        ...d,
                        rows: [...d.rows, { field: fields[0] ?? "", op: ">", value: "" }],
                      }))
                    }
                  >
                    Add condition
                  </Button>
                </div>
              )}
            </div>

            <div>
              <div className="mb-1 text-xs text-slate-500">
                Then, in order… (write {"{{field}}"} in a subject or body to insert the
                record's value — e.g. {"{{days_quiet}}"})
              </div>
              <div className="space-y-2">
                {draft.actions.map((a, i) => (
                  <div key={i} className="rounded-lg border border-slate-200 p-3">
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-slate-400">{i + 1}.</span>
                      <select
                        className={input}
                        value={a.kind}
                        onChange={(e) => updateAction(i, { kind: e.target.value })}
                        aria-label={`Action ${i + 1} kind`}
                      >
                        {(meta.data?.actions ?? Object.keys(ACTION_LABEL)).map((k) => (
                          <option key={k} value={k}>
                            {ACTION_LABEL[k] ?? k}
                          </option>
                        ))}
                      </select>
                      <div className="flex-1" />
                      <Button size="sm" variant="ghost" onClick={() => moveAction(i, -1)}>
                        ↑
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => moveAction(i, 1)}>
                        ↓
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() =>
                          setDraft((d) => ({
                            ...d,
                            actions: d.actions.filter((_, j) => j !== i),
                          }))
                        }
                      >
                        Remove
                      </Button>
                    </div>
                    <div className="mt-2 space-y-2">
                      {a.kind !== "create_customer_note" && (
                        <input
                          className={`${input} w-full`}
                          value={a.subject ?? ""}
                          onChange={(e) => updateAction(i, { subject: e.target.value })}
                          placeholder="Subject"
                          aria-label={`Action ${i + 1} subject`}
                        />
                      )}
                      <textarea
                        className={`${input} h-20 w-full`}
                        value={a.body ?? ""}
                        onChange={(e) => updateAction(i, { body: e.target.value })}
                        placeholder="Body"
                        aria-label={`Action ${i + 1} body`}
                      />
                    </div>
                  </div>
                ))}
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() =>
                    setDraft((d) => ({
                      ...d,
                      actions: [...d.actions, { kind: "notify_owner_email", subject: "", body: "" }],
                    }))
                  }
                >
                  Add action
                </Button>
              </div>
            </div>

            <div className="flex items-center gap-2">
              <Button size="sm" loading={save.isPending} onClick={() => save.mutate()}>
                {selected ? "Save changes" : "Create draft"}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
                Cancel
              </Button>
              {selected && selectedRule && (selectedRule.published_version ?? 0) > 1 && (
                <Button
                  size="sm"
                  variant="ghost"
                  loading={revert.isPending}
                  onClick={() =>
                    revert.mutate({
                      id: selected,
                      version: (selectedRule.published_version ?? 2) - 1,
                    })
                  }
                >
                  Revert to v{(selectedRule.published_version ?? 2) - 1}
                </Button>
              )}
              {selectedRule?.status === "published" && (
                <span className="text-xs text-slate-400">
                  Saving edits creates a new draft state — publish again to make them live.
                </span>
              )}
            </div>
          </div>
        </Card>
      )}

      {dryRun && (
        <Card>
          <div className="mb-2 flex items-center justify-between">
            <h2 className="font-medium">Dry run — {dryRun.rule}</h2>
            <Button size="sm" variant="ghost" onClick={() => setDryRun(null)}>
              Close
            </Button>
          </div>
          {dryRun.outcomes.length === 0 ? (
            <p className="text-sm text-slate-400">
              Nothing matches right now — no record satisfies the trigger and condition.
            </p>
          ) : (
            <ul className="space-y-1 text-sm">
              {dryRun.outcomes.map((o, i) => (
                <li key={i} className="rounded bg-slate-50 px-2 py-1 font-mono text-xs">
                  {JSON.stringify(o)}
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}

      <Card>
        <h2 className="mb-3 font-medium">Recent runs</h2>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-slate-400">
              <th className="py-1">Rule</th>
              <th className="py-1">Record</th>
              <th className="py-1">Outcome</th>
              <th className="py-1">Version</th>
              <th className="py-1">At</th>
            </tr>
          </thead>
          <tbody>
            {runList.map((r) => (
              <tr key={r.id} className="border-t border-slate-100">
                <td className="py-1.5">{ruleName.get(r.rule_id) ?? r.rule_id.slice(0, 8)}</td>
                <td className="py-1.5 font-mono text-xs">{r.ref_id.slice(0, 12)}</td>
                <td className="py-1.5">
                  <Badge
                    tone={
                      r.status === "ok" ? "success" : r.status === "throttled" ? "warning" : "danger"
                    }
                  >
                    {r.status}
                  </Badge>
                </td>
                <td className="py-1.5">v{r.version}</td>
                <td className="py-1.5 text-slate-500">{new Date(r.at).toLocaleString()}</td>
              </tr>
            ))}
            {runList.length === 0 && (
              <tr>
                <td colSpan={5} className="py-3 text-slate-400">
                  No runs yet — runs appear after a published rule's first sweep.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
