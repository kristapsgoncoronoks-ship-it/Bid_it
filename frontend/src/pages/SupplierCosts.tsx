import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { KpiCard } from "../components/KpiCard";
import { api, apiError } from "../lib/api";
import { useConfirm } from "../components/ui/useConfirm";

/** Supplier cost analytics, phase 1 (WO-G): what you pay per supplier and
 * item, how it moved, and the graph behind any row — read models over the
 * invoices already captured, nothing external. The % compares the latest
 * price against the weighted average of everything before it. */

interface CostRow {
  vendor_id: string;
  vendor_name: string;
  item: string;
  category: string;
  points: number;
  latest_price: string;
  latest_date: string;
  trailing_avg: string;
  pct_change: string;
}

interface ChangesOut {
  currency: string;
  available_currencies: string[];
  window_days: number;
  total_tracked: number;
  rows: CostRow[];
}

interface KpisOut {
  currency: string;
  suppliers: number;
  tracked_items: number;
  risers: number;
  fallers: number;
  biggest_mover: CostRow | null;
}

interface HistoryOut {
  currency: string;
  series: { month: string; avg_price: string; quantity: string; spend: string; points: number }[];
}

export default function SupplierCosts() {
  const [selected, setSelected] = useState<CostRow | null>(null);

  const kpis = useQuery<KpisOut>({
    queryKey: ["supplier-costs", "kpis"],
    queryFn: async () => (await api.get("/analytics/supplier-costs/kpis")).data,
  });
  const changes = useQuery<ChangesOut>({
    queryKey: ["supplier-costs", "changes"],
    queryFn: async () => (await api.get("/analytics/supplier-costs/changes")).data,
  });

  const k = kpis.data;
  const c = changes.data;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Supplier costs</h1>
        <p className="text-sm text-slate-500">
          What you actually pay, per supplier and item, from the invoices already
          captured — and how it moved. The change compares the latest price with
          the weighted average of everything before it
          {c ? ` (last ${Math.round(c.window_days / 30)} months, ${c.currency})` : ""}.
        </p>
        {c && c.available_currencies.length > 1 && (
          <p className="mt-1 text-xs text-amber-600">
            Purchases in {c.available_currencies.filter((x) => x !== c.currency).join(", ")} are
            not folded into these {c.currency} figures.
          </p>
        )}
      </div>

      {k && (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <KpiCard label="Suppliers tracked" value={String(k.suppliers)} />
          <KpiCard label="Items with history" value={String(k.tracked_items)} />
          <KpiCard
            label="Prices up / down"
            value={`${k.risers} / ${k.fallers}`}
            accent={k.risers > k.fallers ? "rose" : "emerald"}
          />
          <KpiCard
            label="Biggest move"
            value={k.biggest_mover ? `${k.biggest_mover.pct_change}%` : "—"}
            sub={k.biggest_mover ? `${k.biggest_mover.item} · ${k.biggest_mover.vendor_name}` : "needs two purchases of the same item"}
            accent={
              k.biggest_mover && Number(k.biggest_mover.pct_change) > 0 ? "rose" : "emerald"
            }
          />
        </div>
      )}

      <div className="card p-6">
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
          Cost changes
        </h2>
        {!c || c.rows.length === 0 ? (
          <p className="text-sm text-slate-400">
            Nothing to compare yet — an item enters this list after two purchases
            with a quantity and a price.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wide text-slate-400">
                <tr>
                  <th className="py-2">Supplier</th>
                  <th className="py-2">Item</th>
                  <th className="py-2 text-right">Purchases</th>
                  <th className="py-2 text-right">Was (avg)</th>
                  <th className="py-2 text-right">Now</th>
                  <th className="py-2 text-right">Change</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {c.rows.map((r) => {
                  const up = Number(r.pct_change) > 0;
                  return (
                    <tr
                      key={`${r.vendor_id}:${r.item}`}
                      className={`cursor-pointer hover:bg-slate-50 ${
                        selected && selected.vendor_id === r.vendor_id && selected.item === r.item
                          ? "bg-slate-50"
                          : ""
                      }`}
                      onClick={() => setSelected(r)}
                    >
                      <td className="py-2 text-slate-700">{r.vendor_name}</td>
                      <td className="py-2 text-slate-700">{r.item}</td>
                      <td className="py-2 text-right text-slate-400">{r.points}</td>
                      <td className="py-2 text-right tabular-nums">{r.trailing_avg}</td>
                      <td className="py-2 text-right font-medium tabular-nums">{r.latest_price}</td>
                      <td
                        className={`py-2 text-right font-semibold tabular-nums ${
                          up ? "text-rose-600" : "text-emerald-600"
                        }`}
                      >
                        {up ? "+" : ""}
                        {r.pct_change}%
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {c.total_tracked > c.rows.length && (
              <p className="mt-2 text-xs text-slate-400">
                Showing the {c.rows.length} biggest moves of {c.total_tracked} tracked items.
              </p>
            )}
          </div>
        )}
      </div>

      {selected && <HistoryChart row={selected} />}

      <AgreedPrices />
      <Overcharges />
    </div>
  );
}

interface AgreedRow {
  id: string;
  vendor_id: string;
  vendor_name: string;
  item: string;
  currency: string;
  agreed_price: string;
  valid_from: string;
  valid_to: string | null;
  note: string | null;
}

/** Phase 2: the price list the system holds suppliers to. Advisory findings
 * everywhere; the submit gate blocks only when Settings → "Block overcharges"
 * is on. */
function AgreedPrices() {
  const { confirm, dialog } = useConfirm();
  const qc = useQueryClient();
  const [vendorId, setVendorId] = useState("");
  const [item, setItem] = useState("");
  const [price, setPrice] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const vendors = useQuery<{ id: string; name: string }[]>({
    queryKey: ["vendors"],
    queryFn: async () => (await api.get("/vendors")).data,
  });
  const agreed = useQuery<AgreedRow[]>({
    queryKey: ["supplier-costs", "agreed"],
    queryFn: async () => (await api.get("/analytics/supplier-costs/agreed")).data,
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["supplier-costs", "agreed"] });
    qc.invalidateQueries({ queryKey: ["supplier-costs", "overcharges"] });
  };

  const save = useMutation({
    mutationFn: async () =>
      (
        await api.put("/analytics/supplier-costs/agreed", {
          vendor_id: vendorId,
          item,
          agreed_price: price,
        })
      ).data,
    onSuccess: () => {
      setErr(null);
      setItem("");
      setPrice("");
      refresh();
    },
    onError: (e) => setErr(apiError(e)),
  });
  const remove = useMutation({
    mutationFn: async (id: string) =>
      (await api.delete(`/analytics/supplier-costs/agreed/${id}`)).data,
    onSuccess: refresh,
    onError: (e) => setErr(apiError(e)),
  });

  const vendorList = Array.isArray(vendors.data) ? vendors.data : [];
  const rows = Array.isArray(agreed.data) ? agreed.data : [];
  const input = "rounded-lg border border-slate-300 px-2 py-1 text-sm";

  return (
    <div className="card p-6">
      {dialog}
      <h2 className="mb-1 text-sm font-semibold uppercase tracking-wide text-slate-500">
        Agreed prices
      </h2>
      <p className="mb-3 text-xs text-slate-400">
        What a unit price should be, per supplier and item (the item is matched
        against invoice line descriptions). Lines priced above this are flagged —
        and refused at submit if you turn on "Block overcharges" in Settings.
      </p>
      {err && (
        <div className="mb-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
          {err}
        </div>
      )}
      {rows.length > 0 && (
        <table className="mb-4 w-full text-sm">
          <thead className="text-left text-xs uppercase tracking-wide text-slate-400">
            <tr>
              <th className="py-1">Supplier</th>
              <th className="py-1">Item</th>
              <th className="py-1 text-right">Agreed</th>
              <th className="py-1">From</th>
              <th className="py-1">Until</th>
              <th className="py-1"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {rows.map((r) => (
              <tr key={r.id}>
                <td className="py-1.5 text-slate-700">{r.vendor_name}</td>
                <td className="py-1.5 text-slate-700">{r.item}</td>
                <td className="py-1.5 text-right font-medium tabular-nums">
                  {r.agreed_price} {r.currency}
                </td>
                <td className="py-1.5 text-slate-500">{r.valid_from}</td>
                <td className="py-1.5 text-slate-500">{r.valid_to ?? "open"}</td>
                <td className="py-1.5 text-right">
                  <button
                    className="text-xs text-slate-400 hover:text-rose-600"
                    onClick={async () => { if (await confirm({ title: "Remove this agreed price?", body: "Overcharge checks stop comparing against it from now on. Past overcharge findings are kept.", confirmLabel: "Remove" })) remove.mutate(r.id); }}
                  >
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <select
          className={input}
          value={vendorId}
          onChange={(e) => setVendorId(e.target.value)}
          aria-label="Agreed price supplier"
        >
          <option value="">Supplier…</option>
          {vendorList.map((v) => (
            <option key={v.id} value={v.id}>
              {v.name}
            </option>
          ))}
        </select>
        <input
          className={`${input} w-56`}
          placeholder="Item (as it appears on invoices)"
          value={item}
          onChange={(e) => setItem(e.target.value)}
          aria-label="Agreed price item"
        />
        <input
          className={`${input} w-28`}
          placeholder="Price"
          value={price}
          onChange={(e) => setPrice(e.target.value)}
          aria-label="Agreed price value"
        />
        <button
          className="rounded-lg bg-indigo-600 px-3 py-1 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
          disabled={!vendorId || !item.trim() || !price.trim() || save.isPending}
          onClick={() => save.mutate()}
        >
          Set agreed price
        </button>
      </div>
    </div>
  );
}

interface OverchargeRow {
  invoice_id: string;
  invoice_number: string;
  issue_date: string;
  currency: string;
  vendor_name: string;
  item: string;
  quantity: string;
  unit_price: string;
  agreed_price: string;
  delta_per_unit: string;
  overcharge: string;
}

function Overcharges() {
  const work = useQuery<{ total_overcharge: string; rows: OverchargeRow[] }>({
    queryKey: ["supplier-costs", "overcharges"],
    queryFn: async () => (await api.get("/analytics/supplier-costs/overcharges")).data,
  });
  const rows = Array.isArray(work.data?.rows) ? work.data.rows : [];
  if (rows.length === 0) return null;
  return (
    <div className="card p-6">
      <h2 className="mb-1 text-sm font-semibold uppercase tracking-wide text-slate-500">
        Overcharges
      </h2>
      <p className="mb-3 text-xs text-slate-400">
        Captured lines priced above the agreement in force on their invoice date —{" "}
        <span className="font-semibold text-rose-600">
          {work.data?.total_overcharge} overcharged
        </span>{" "}
        in the last 12 months.
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase tracking-wide text-slate-400">
            <tr>
              <th className="py-1">Invoice</th>
              <th className="py-1">Supplier</th>
              <th className="py-1">Item</th>
              <th className="py-1 text-right">Qty</th>
              <th className="py-1 text-right">Paid</th>
              <th className="py-1 text-right">Agreed</th>
              <th className="py-1 text-right">Overcharge</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {rows.map((r, i) => (
              <tr key={`${r.invoice_id}:${r.item}:${i}`}>
                <td className="py-1.5 text-slate-700">{r.invoice_number}</td>
                <td className="py-1.5 text-slate-700">{r.vendor_name}</td>
                <td className="py-1.5 text-slate-700">{r.item}</td>
                <td className="py-1.5 text-right tabular-nums">{r.quantity}</td>
                <td className="py-1.5 text-right tabular-nums">{r.unit_price}</td>
                <td className="py-1.5 text-right tabular-nums">{r.agreed_price}</td>
                <td className="py-1.5 text-right font-semibold tabular-nums text-rose-600">
                  {r.overcharge}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function HistoryChart({ row }: { row: CostRow }) {
  const hist = useQuery<HistoryOut>({
    queryKey: ["supplier-costs", "history", row.vendor_id, row.item],
    queryFn: async () =>
      (
        await api.get("/analytics/supplier-costs/history", {
          params: { vendor_id: row.vendor_id, item: row.item, months: 12 },
        })
      ).data,
  });

  const data = (hist.data?.series ?? []).map((p) => ({
    month: p.month,
    price: Number(p.avg_price),
  }));

  return (
    <div className="card p-6">
      <h2 className="mb-1 text-sm font-semibold uppercase tracking-wide text-slate-500">
        Price history — {row.item}
      </h2>
      <p className="mb-3 text-xs text-slate-400">
        {row.vendor_name} · monthly weighted average unit price
        {hist.data ? ` (${hist.data.currency})` : ""}
      </p>
      {data.length === 0 ? (
        <p className="text-sm text-slate-400">No purchases in the last 12 months.</p>
      ) : (
        <ResponsiveContainer width="100%" height={240}>
          <LineChart data={data} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
            <XAxis dataKey="month" tick={{ fontSize: 12 }} />
            <YAxis tick={{ fontSize: 12 }} domain={["auto", "auto"]} />
            <Tooltip />
            <Line
              type="monotone"
              dataKey="price"
              stroke="#4f46e5"
              strokeWidth={2}
              dot={{ r: 3 }}
            />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
