import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Button, Card } from "../components/ui";
import { api, apiError } from "../lib/api";
import type { DunningLevel, DunningPolicy } from "../lib/types";

const TONES: DunningLevel["tone"][] = ["reminder", "firm", "final"];
const TONE_LABEL: Record<DunningLevel["tone"], string> = {
  reminder: "Reminder (gentle)",
  firm: "Firm (overdue notice)",
  final: "Final notice",
};

export default function DunningSettingsPage() {
  const qc = useQueryClient();
  const [rows, setRows] = useState<DunningLevel[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const policy = useQuery<DunningPolicy>({
    queryKey: ["dunning-policy"],
    queryFn: async () => (await api.get("/dunning/policy")).data,
  });

  useEffect(() => {
    if (policy.data) setRows(policy.data.levels.map((l) => ({ ...l })));
  }, [policy.data]);

  const save = useMutation({
    mutationFn: async () => {
      const levels = rows
        .map((r, i) => ({ ...r, level: i + 1 }))
        .sort((a, b) => a.days_overdue - b.days_overdue)
        .map((r, i) => ({ ...r, level: i + 1 }));
      return (await api.put("/dunning/policy", { levels })).data as DunningPolicy;
    },
    onSuccess: () => {
      setErr(null);
      setSaved(true);
      qc.invalidateQueries({ queryKey: ["dunning-policy"] });
      setTimeout(() => setSaved(false), 2500);
    },
    onError: (e) => setErr(apiError(e)),
  });

  const update = (i: number, patch: Partial<DunningLevel>) =>
    setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const addRow = () =>
    setRows([
      ...rows,
      { level: rows.length + 1, days_overdue: 30, tone: "final", active: true },
    ]);
  const removeRow = (i: number) => setRows(rows.filter((_, j) => j !== i));
  const resetDefaults = () => setRows([]);

  if (policy.isLoading) return <div className="text-slate-400">Loading…</div>;

  const input = "rounded-lg border border-slate-300 px-2 py-1 text-sm";
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Dunning ladder</h1>
        <p className="text-slate-500">
          The escalation steps for overdue-invoice reminders. Each level is sent once,
          when an invoice reaches that many days past due — so debtors escalate through
          the ladder instead of getting the same reminder every day.
        </p>
      </div>

      {err && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-700">
          {err}
        </div>
      )}
      {policy.data?.is_default && rows.length > 0 && (
        <div className="rounded-lg border border-sky-200 bg-sky-50 px-4 py-2 text-sm text-sky-700">
          Using the built-in default ladder. Adjust and save to customise it.
        </div>
      )}

      <Card>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-slate-400">
              <th className="py-1">Step</th>
              <th className="py-1">Days overdue</th>
              <th className="py-1">Tone</th>
              <th className="py-1">Active</th>
              <th className="py-1"></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-t border-slate-100">
                <td className="py-1 font-medium">{i + 1}</td>
                <td className="py-1">
                  <input
                    type="number"
                    min={0}
                    className={`${input} w-24`}
                    value={r.days_overdue}
                    onChange={(e) => update(i, { days_overdue: Number(e.target.value) })}
                  />
                </td>
                <td className="py-1">
                  <select
                    className={input}
                    value={r.tone}
                    onChange={(e) => update(i, { tone: e.target.value as DunningLevel["tone"] })}
                  >
                    {TONES.map((t) => (
                      <option key={t} value={t}>
                        {TONE_LABEL[t]}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="py-1">
                  <input
                    type="checkbox"
                    checked={r.active}
                    onChange={(e) => update(i, { active: e.target.checked })}
                    aria-label={`Level ${i + 1} active`}
                  />
                </td>
                <td className="py-1 text-right">
                  <Button size="sm" variant="ghost" onClick={() => removeRow(i)}>
                    Remove
                  </Button>
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={5} className="py-2 text-slate-400">
                  No levels — dunning is disabled. Add a level or reset to defaults.
                </td>
              </tr>
            )}
          </tbody>
        </table>
        <div className="mt-4 flex items-center gap-2">
          <Button size="sm" variant="secondary" onClick={addRow}>
            Add level
          </Button>
          <Button size="sm" variant="ghost" onClick={resetDefaults}>
            Reset to defaults
          </Button>
          <div className="flex-1" />
          {saved && <span className="text-sm text-emerald-600">Saved</span>}
          <Button size="sm" loading={save.isPending} onClick={() => save.mutate()}>
            Save ladder
          </Button>
        </div>
      </Card>

      <LateInterestCard />
    </div>
  );
}

interface LateInterestSettings {
  base_rate_pp: string | null;
  default_base_rate_pp: string;
  statutory_margin_pp: string;
}

/** WO-K: the reference rate behind the ADVISORY statutory late-payment figure
 * (Dir. 2011/7/EU: this + 8 pp, plus the fixed €40 recovery cost). Typed by an
 * admin because the platform makes no external calls to fetch it. */
function LateInterestCard() {
  const qc = useQueryClient();
  const [rate, setRate] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const settings = useQuery<LateInterestSettings>({
    queryKey: ["late-interest-settings"],
    queryFn: async () => (await api.get("/settings/late-interest")).data,
  });

  const save = useMutation({
    mutationFn: async () =>
      (
        await api.put(
          "/settings/late-interest",
          rate.trim() === "" ? { clear_base_rate: true } : { base_rate_pp: rate.trim() },
        )
      ).data as LateInterestSettings,
    onSuccess: () => {
      setErr(null);
      qc.invalidateQueries({ queryKey: ["late-interest-settings"] });
    },
    onError: (e) => setErr(apiError(e)),
  });

  const s = settings.data;
  return (
    <Card>
      <h2 className="mb-1 font-medium">Statutory late-payment interest</h2>
      <p className="mb-3 text-sm text-slate-500">
        Overdue invoices with no contractual rate show an advisory figure under
        Directive 2011/7/EU: your central-bank reference rate plus{" "}
        {s?.statutory_margin_pp ?? "8"} points, and the fixed €40 recovery cost.
        Enter the current ECB main refinancing rate here — the platform never
        fetches it for you. Leave empty to use the built-in default (
        {s?.default_base_rate_pp ?? "…"}%).
      </p>
      {err && (
        <div className="mb-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
          {err}
        </div>
      )}
      <div className="flex items-center gap-2">
        <input
          type="text"
          inputMode="decimal"
          className="w-28 rounded-lg border border-slate-300 px-2 py-1 text-sm"
          placeholder={s?.base_rate_pp ?? s?.default_base_rate_pp ?? ""}
          value={rate}
          onChange={(e) => setRate(e.target.value)}
          aria-label="Reference rate percent"
        />
        <span className="text-sm text-slate-400">% p.a.</span>
        <Button size="sm" loading={save.isPending} onClick={() => save.mutate()}>
          Save rate
        </Button>
        {s?.base_rate_pp != null && (
          <span className="text-xs text-slate-400">configured: {s.base_rate_pp}%</span>
        )}
      </div>
    </Card>
  );
}
