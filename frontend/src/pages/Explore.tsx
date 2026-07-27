import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import {
  Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, Pie, PieChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api, downloadFile } from "../lib/api";
import { CHART_PALETTE as PALETTE, compactMoney, money } from "../lib/format";
import type { ExploreCatalog, ExploreResult } from "../lib/types";

type ChartType = "bar" | "line" | "pie" | "stacked" | "table";

export default function Explore() {
  const [measure, setMeasure] = useState("net");
  const [dim0, setDim0] = useState("category");
  const [dim1, setDim1] = useState("");
  const [chart, setChart] = useState<ChartType>("bar");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [status, setStatus] = useState("");

  const fields = useQuery<ExploreCatalog>({ queryKey: ["explore", "fields"], queryFn: async () => (await api.get("/analytics/fields")).data });

  const params = useMemo(() => {
    const p = new URLSearchParams({ measure, dim: dim0 });
    if (dim1) p.append("dim", dim1);
    if (start) p.set("start", start);
    if (end) p.set("end", end);
    if (status) p.set("status", status);
    return p;
  }, [measure, dim0, dim1, start, end, status]);

  const result = useQuery<ExploreResult>({
    queryKey: ["explore", params.toString()],
    queryFn: async () => (await api.get(`/analytics/explore?${params.toString()}`)).data,
    enabled: !!fields.data,
  });

  const unit = result.data?.measure.unit ?? "money";
  const fmt = (v: number) => (unit === "money" ? money(v) : unit === "count" ? String(Math.round(v)) : v.toLocaleString());
  const fmtAxis = (v: number) => (unit === "money" ? compactMoney(v) : String(v));
  const dim0Temporal = fields.data?.dimensions.find((d) => d.key === dim0)?.temporal;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Explore</h1>
        <p className="text-sm text-slate-500">Self-service analytics — pick a measure and dimensions, slice with filters, visualize. Aggregated in the database.</p>
      </div>

      {/* Field pickers */}
      <div className="card grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-6">
        <Picker label="Measure" value={measure} onChange={setMeasure} options={(fields.data?.measures ?? []).map((m) => [m.key, m.label])} />
        <Picker label="Dimension" value={dim0} onChange={setDim0} options={(fields.data?.dimensions ?? []).map((d) => [d.key, d.label])} />
        <Picker label="Break by" value={dim1} onChange={setDim1} options={[["", "— none —"], ...(fields.data?.dimensions ?? []).filter((d) => d.key !== dim0).map((d) => [d.key, d.label] as [string, string])]} />
        <Picker label="Chart" value={chart} onChange={(v) => setChart(v as ChartType)} options={[["bar", "Bar"], ["line", "Line"], ["pie", "Pie"], ["stacked", "Stacked"], ["table", "Table"]]} />
        <div>
          <label className="label">From</label>
          <input type="date" className="input" value={start} onChange={(e) => setStart(e.target.value)} />
        </div>
        <div>
          <label className="label">To</label>
          <input type="date" className="input" value={end} onChange={(e) => setEnd(e.target.value)} />
        </div>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <Picker label="Status filter" value={status} onChange={setStatus} options={[["", "All"], ["paid", "paid"], ["pending", "pending"], ["overdue", "overdue"], ["draft", "draft"]]} className="w-40" />
        <button className="btn-ghost" onClick={() => downloadFile(`/analytics/explore?${params.toString()}&format=csv`, "explore.csv")}>Export CSV</button>
        <button className="btn-ghost" onClick={() => downloadFile(`/analytics/explore?${params.toString()}&format=xlsx`, "explore.xlsx")}>Export Excel</button>
        <button className="btn-ghost" onClick={() => downloadFile(`/analytics/explore?${params.toString()}&format=pdf`, "explore.pdf")}>Export PDF</button>
      </div>

      <div className="card">
        <div className="mb-2 text-sm font-semibold text-slate-600">
          {result.data?.measure.label} by {result.data?.dimensions.map((d) => d.label).join(" × ")}
        </div>
        {result.isLoading || !result.data ? (
          <div className="grid h-72 place-items-center text-slate-400">Loading…</div>
        ) : result.data.rows.length === 0 ? (
          <div className="grid h-72 place-items-center text-slate-400">No data for this selection.</div>
        ) : (
          <ChartView result={result.data} chart={chart} fmt={fmt} fmtAxis={fmtAxis} temporal={dim0Temporal} />
        )}
      </div>

      {result.data && result.data.rows.length > 0 && <DataTable result={result.data} fmt={fmt} />}
    </div>
  );
}

function ChartView({ result, chart, fmt, fmtAxis, temporal }: {
  result: ExploreResult; chart: ChartType; fmt: (v: number) => string; fmtAxis: (v: number) => string; temporal?: boolean;
}) {
  const [d0, d1] = result.dimensions;
  const single = result.dimensions.length === 1;

  // 2-dim → pivot into stacked series
  const { data, series } = useMemo(() => {
    if (single) {
      return { data: result.rows.map((r) => ({ name: r[d0.key], value: Number(r.value) })), series: [] as string[] };
    }
    const seriesSet: string[] = [];
    const byX: Record<string, any> = {};
    for (const r of result.rows) {
      const x = r[d0.key], s = r[d1!.key];
      if (!seriesSet.includes(s)) seriesSet.push(s);
      byX[x] = byX[x] || { name: x };
      byX[x][s] = Number(r.value);
    }
    return { data: Object.values(byX), series: seriesSet };
  }, [result, single, d0, d1]);

  // Temporal single-dimension defaults to a line unless the user picked a chart.
  const singleChart = single && chart === "stacked" ? (temporal ? "line" : "bar") : chart;

  if (single && singleChart === "line") {
    return (
      <ResponsiveContainer width="100%" height={320}>
        <LineChart data={data} margin={{ top: 8, right: 12, left: 8, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" />
          <XAxis dataKey="name" tick={{ fontSize: 12, fill: "#94a3b8" }} />
          <YAxis tickFormatter={fmtAxis} tick={{ fontSize: 12, fill: "#94a3b8" }} width={64} />
          <Tooltip formatter={(v) => fmt(Number(v))} />
          <Line type="monotone" dataKey="value" stroke="#3b6ef2" strokeWidth={2.5} dot={{ r: 2 }} />
        </LineChart>
      </ResponsiveContainer>
    );
  }
  if (single && singleChart === "pie") {
    return (
      <ResponsiveContainer width="100%" height={320}>
        <PieChart>
          <Pie data={data} dataKey="value" nameKey="name" innerRadius={60} outerRadius={110} paddingAngle={2}>
            {data.map((_, i) => <Cell key={i} fill={PALETTE[i % PALETTE.length]} />)}
          </Pie>
          <Tooltip formatter={(v) => fmt(Number(v))} />
        </PieChart>
      </ResponsiveContainer>
    );
  }
  if (single) {
    return (
      <ResponsiveContainer width="100%" height={320}>
        <BarChart data={data} margin={{ top: 8, right: 12, left: 8, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" />
          <XAxis dataKey="name" tick={{ fontSize: 12, fill: "#94a3b8" }} />
          <YAxis tickFormatter={fmtAxis} tick={{ fontSize: 12, fill: "#94a3b8" }} width={64} />
          <Tooltip formatter={(v) => fmt(Number(v))} />
          <Bar dataKey="value" radius={[4, 4, 0, 0]}>
            {data.map((_, i) => <Cell key={i} fill={PALETTE[i % PALETTE.length]} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    );
  }
  // two dimensions → stacked bar
  return (
    <ResponsiveContainer width="100%" height={340}>
      <BarChart data={data} margin={{ top: 8, right: 12, left: 8, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" />
        <XAxis dataKey="name" tick={{ fontSize: 12, fill: "#94a3b8" }} />
        <YAxis tickFormatter={fmtAxis} tick={{ fontSize: 12, fill: "#94a3b8" }} width={64} />
        <Tooltip formatter={(v) => fmt(Number(v))} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        {series.map((s, i) => (
          <Bar key={s} dataKey={s} stackId="a" fill={PALETTE[i % PALETTE.length]} />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}

function DataTable({ result, fmt }: { result: ExploreResult; fmt: (v: number) => string }) {
  return (
    <div className="card overflow-x-auto p-0">
      <table className="w-full text-sm">
        <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
          <tr>
            {result.dimensions.map((d) => <th key={d.key} className="px-4 py-3">{d.label}</th>)}
            <th className="px-4 py-3 text-right">{result.measure.label}</th>
          </tr>
        </thead>
        <tbody>
          {result.rows.slice(0, 200).map((r, i) => (
            <tr key={i} className="border-b border-slate-100 last:border-0 hover:bg-slate-50">
              {result.dimensions.map((d) => <td key={d.key} className="px-4 py-2">{r[d.key]}</td>)}
              <td className="px-4 py-2 text-right font-medium">{fmt(Number(r.value))}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Picker({ label, value, onChange, options, className }: {
  label: string; value: string; onChange: (v: string) => void; options: [string, string][]; className?: string;
}) {
  return (
    <div className={className}>
      <label className="label">{label}</label>
      <select className="input" value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </div>
  );
}
