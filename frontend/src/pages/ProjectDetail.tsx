import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Badge, Button, QueryState, Skeleton } from "../components/ui";
import { api, apiError, downloadFile } from "../lib/api";
import { shortDate } from "../lib/format";
import type { CostEntry, ProjectDocument, ProjectPnl } from "../lib/types";

/**
 * One project's profitability (docs/design/project-profitability.md, phase 1).
 *
 * The screen a project-shaped business runs on: revenue − costs for one won
 * contract/job. Industry-neutral by owner requirement — nothing here names an
 * industry, and the copy speaks of projects, work and costs.
 *
 * Two rules inherited from the rest of the product:
 *  - the P&L's BASIS comes off the wire (`basis`), never asserted locally —
 *    this screen says "live figures" because the server says so, and when
 *    phase 2 freezes closed projects the copy follows the field, not a deploy;
 *  - money renders as the wire's decimal strings. No float ever touches it.
 */
const CATEGORIES = [
  { value: "wages", label: "Wages" },
  { value: "per_diem", label: "Per diem" },
  { value: "equipment", label: "Equipment" },
  { value: "other", label: "Other" },
];

function Money({ value, negative }: { value: string; negative?: boolean }) {
  return (
    <span className={`tabular-nums ${negative ? "text-rose-600" : "text-slate-800"}`}>
      {value} €
    </span>
  );
}

export default function ProjectDetail() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const [label, setLabel] = useState("");
  const [category, setCategory] = useState("wages");
  const [amount, setAmount] = useState("");

  const pnl = useQuery<ProjectPnl>({
    queryKey: ["project-pnl", id],
    queryFn: async () => (await api.get(`/masters/projects/${id}/pnl`)).data,
  });
  const entries = useQuery<CostEntry[]>({
    queryKey: ["project-cost-entries", id],
    queryFn: async () => (await api.get(`/masters/projects/${id}/cost-entries`)).data,
  });
  const docs = useQuery<ProjectDocument[]>({
    queryKey: ["project-documents", id],
    queryFn: async () => (await api.get(`/masters/projects/${id}/documents`)).data,
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["project-pnl", id] });
    qc.invalidateQueries({ queryKey: ["project-cost-entries", id] });
  };

  const addEntry = useMutation({
    mutationFn: async () =>
      (
        await api.post(`/masters/projects/${id}/cost-entries`, {
          label,
          category,
          amount,
        })
      ).data,
    onSuccess: () => {
      setLabel("");
      setAmount("");
      setErr(null);
      refresh();
    },
    onError: (e) => setErr(apiError(e)),
  });

  const removeEntry = useMutation({
    mutationFn: async (entryId: string) =>
      api.delete(`/masters/projects/${id}/cost-entries/${entryId}`),
    onSuccess: () => {
      setErr(null);
      refresh();
    },
    onError: (e) => setErr(apiError(e)),
  });

  const upload = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return (await api.post(`/masters/projects/${id}/documents`, form)).data;
    },
    onSuccess: () => {
      setErr(null);
      qc.invalidateQueries({ queryKey: ["project-documents", id] });
    },
    onError: (e) => setErr(apiError(e)),
  });

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            {pnl.data ? `${pnl.data.code} · ${pnl.data.name}` : "Project"}
          </h1>
          <p className="text-sm text-slate-500">
            Everything this project earned and cost — sales invoices issued under
            it, supplier and subcontractor invoices allocated to it, approved
            expenses, and costs booked by hand.
          </p>
        </div>
        <Link to="/cost-objects" className="btn-secondary shrink-0">
          All projects
        </Link>
      </div>

      {err && (
        <div className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {err}
        </div>
      )}

      <QueryState
        query={pnl}
        loading={<Skeleton className="h-40 w-full" />}
        errorTitle="Couldn’t load the project"
      >
        {(data) => (
          <div className="card p-6">
            <div className="mb-4 flex items-center gap-3">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
                Profitability
              </h2>
              <Badge tone={data.status === "active" ? "success" : "neutral"}>{data.status}</Badge>
              {/* The basis is the server's word, not this screen's. */}
              {data.basis === "net_eur_live" && (
                <span className="text-xs text-slate-400">
                  live figures · net amounts, EUR
                </span>
              )}
              {data.basis === "net_eur_frozen" && (
                <span className="text-xs text-slate-400">
                  frozen at close
                  {data.pnl_frozen_at ? ` (${shortDate(data.pnl_frozen_at)})` : ""} · net
                  amounts, EUR
                </span>
              )}
            </div>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <div>
                <p className="text-xs uppercase tracking-wide text-slate-400">Revenue</p>
                <p className="text-xl font-semibold">
                  <Money value={data.revenue} />
                </p>
                {data.credited !== "0.00" && (
                  <p className="text-xs text-slate-400">after {data.credited} € credited</p>
                )}
              </div>
              <div>
                <p className="text-xs uppercase tracking-wide text-slate-400">Costs</p>
                <p className="text-xl font-semibold">
                  <Money value={data.costs} />
                </p>
                <p className="text-xs text-slate-400">
                  invoices {data.invoice_costs} € · expenses {data.expense_costs} € · manual{" "}
                  {data.manual_costs} €
                </p>
              </div>
              <div>
                <p className="text-xs uppercase tracking-wide text-slate-400">Profit</p>
                <p className="text-xl font-semibold">
                  <Money value={data.profit} negative={data.profit.startsWith("-")} />
                </p>
              </div>
              <div>
                <p className="text-xs uppercase tracking-wide text-slate-400">Margin</p>
                <p className="text-xl font-semibold tabular-nums">
                  {data.margin_pct != null ? `${data.margin_pct}%` : "—"}
                </p>
              </div>
            </div>
            {Object.keys(data.adjustments ?? {}).length > 0 && (
              <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
                <p className="font-medium">Arrived after close</p>
                <p className="text-xs">
                  These changes happened after this project&apos;s figures were frozen.
                  The frozen numbers above are unchanged; reopen the project to
                  recalculate.
                </p>
                <ul className="mt-1 text-xs">
                  {Object.entries(data.adjustments).map(([k, v]) => (
                    <li key={k} className="tabular-nums">
                      {k.replace(/_/g, " ")}: {v} €
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </QueryState>

      <div className="grid gap-6 lg:grid-cols-2">
        <div className="card space-y-4 p-6">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            Costs booked by hand
          </h2>
          <p className="text-sm text-slate-500">
            Costs that never arrive as an invoice or an expense report — wages for
            the job, per diems, equipment hire. A labelled amount, nothing more.
          </p>
          <form
            className="flex flex-wrap items-end gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              addEntry.mutate();
            }}
          >
            <div className="grow">
              <label className="label">Label</label>
              <input
                className="input"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="Wages for the job"
                required
              />
            </div>
            <div>
              <label className="label">Category</label>
              <select
                className="input"
                value={category}
                onChange={(e) => setCategory(e.target.value)}
              >
                {CATEGORIES.map((c) => (
                  <option key={c.value} value={c.value}>
                    {c.label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="label">Amount (EUR)</label>
              <input
                className="input w-32"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                placeholder="300.00"
                required
              />
            </div>
            <Button type="submit" disabled={addEntry.isPending}>
              Add cost
            </Button>
          </form>

          {(entries.data?.length ?? 0) > 0 && (
            <table className="w-full text-sm">
              <tbody className="divide-y divide-slate-100">
                {entries.data?.map((e) => (
                  <tr key={e.id}>
                    <td className="py-2 text-slate-700">{e.label}</td>
                    <td className="py-2 text-slate-400">{e.category}</td>
                    <td className="py-2 text-right tabular-nums text-slate-700">{e.amount} €</td>
                    <td className="py-2 pl-3 text-right">
                      <button
                        className="btn-ghost text-xs"
                        onClick={() => removeEntry.mutate(e.id)}
                        disabled={removeEntry.isPending}
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="card space-y-4 p-6">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            Contract &amp; documents
          </h2>
          <p className="text-sm text-slate-500">
            The signed contract this project fulfils, kept with the numbers it
            explains.
          </p>
          <label className="btn-secondary inline-block cursor-pointer">
            Attach document
            <input
              type="file"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) upload.mutate(f);
                e.target.value = "";
              }}
            />
          </label>
          {(docs.data?.length ?? 0) > 0 && (
            <table className="w-full text-sm">
              <tbody className="divide-y divide-slate-100">
                {docs.data?.map((d) => (
                  <tr key={d.id}>
                    <td className="py-2 text-slate-700">{d.filename}</td>
                    <td className="py-2 text-slate-400">{d.kind}</td>
                    <td className="py-2 text-slate-400">{shortDate(d.created_at)}</td>
                    <td className="py-2 pl-3 text-right">
                      <button
                        className="btn-ghost text-xs"
                        onClick={() =>
                          downloadFile(
                            `/masters/projects/${id}/documents/${d.id}/download`,
                            d.filename,
                          ).catch((e) => setErr(apiError(e)))
                        }
                      >
                        Download
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
