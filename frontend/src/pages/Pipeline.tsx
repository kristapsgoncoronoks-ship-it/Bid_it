import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { Badge } from "../components/ui";

/** CRM light (WO-H): the kanban read over the EXISTING offer pipeline.
 * Columns are the offer statuses we already have — no second pipeline
 * entity. A `sent` offer that sat still past the threshold shows its age
 * in red: the one pipeline signal this segment demonstrably uses. */

interface PipelineRow {
  offer_id: string;
  number: string;
  version: number;
  title: string | null;
  total: string;
  currency: string;
  project_id: string;
  project: string;
  customer: string | null;
  days_in_stage: number;
  stale: boolean;
}

interface PipelineOut {
  stale_after_days: number;
  columns: Record<string, PipelineRow[]>;
}

const COLUMNS: { key: string; label: string; hint: string }[] = [
  { key: "draft", label: "Draft", hint: "being prepared — ours to finish" },
  { key: "sent", label: "Sent", hint: "with the customer — chase when red" },
  { key: "accepted", label: "Won", hint: "accepted — work begins" },
  { key: "rejected", label: "Lost", hint: "declined — reportable, off the board" },
];

export default function Pipeline() {
  const pipe = useQuery<PipelineOut>({
    queryKey: ["offers-pipeline"],
    queryFn: async () => (await api.get("/masters/offers-pipeline")).data,
  });

  const columns = pipe.data?.columns ?? {};
  const total = Object.values(columns).reduce((n, rows) => n + rows.length, 0);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Offer pipeline</h1>
        <p className="text-sm text-slate-500">
          Every offer, by where it stands. An offer that sat with the customer
          for {pipe.data?.stale_after_days ?? 14}+ days without movement turns
          red — that is the one to chase today.
        </p>
      </div>

      {total === 0 ? (
        <div className="card p-6 text-sm text-slate-400">
          No offers yet — create one on any project page and it appears here.
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {COLUMNS.map((col) => {
            const rows = columns[col.key] ?? [];
            return (
              <div key={col.key} className="space-y-2">
                <div className="flex items-baseline justify-between px-1">
                  <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
                    {col.label}
                  </h2>
                  <span className="text-xs text-slate-400">{rows.length}</span>
                </div>
                <p className="px-1 text-xs text-slate-400">{col.hint}</p>
                {rows.map((r) => (
                  <Link
                    key={r.offer_id}
                    to={`/projects/${r.project_id}`}
                    className={`card block space-y-1 p-3 hover:border-brand-300 ${
                      r.stale ? "border-rose-300 bg-rose-50/40" : ""
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-medium text-slate-700">
                        {r.number} v{r.version}
                      </span>
                      {r.stale ? (
                        <Badge tone="danger">{r.days_in_stage}d — chase</Badge>
                      ) : (
                        <span className="text-xs text-slate-400">{r.days_in_stage}d</span>
                      )}
                    </div>
                    {r.title && <p className="truncate text-xs text-slate-500">{r.title}</p>}
                    <p className="truncate text-xs text-slate-400">
                      {r.project}
                      {r.customer ? ` · ${r.customer}` : ""}
                    </p>
                    <p className="text-sm font-semibold tabular-nums text-slate-700">
                      {r.total} {r.currency}
                    </p>
                  </Link>
                ))}
                {rows.length === 0 && (
                  <div className="rounded-lg border border-dashed border-slate-200 p-3 text-center text-xs text-slate-300">
                    empty
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
