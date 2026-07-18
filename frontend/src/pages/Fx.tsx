import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { KpiCard } from "../components/KpiCard";
import { api, apiError } from "../lib/api";
import { money, shortDate } from "../lib/format";
import type { FxComparison, FxConvert, FxRates } from "../lib/types";

export default function Fx() {
  const rates = useQuery<FxRates>({
    queryKey: ["fx", "rates"],
    queryFn: async () => (await api.get("/fx/rates")).data,
  });
  const comparison = useQuery<FxComparison>({
    queryKey: ["fx", "comparison"],
    queryFn: async () => (await api.get("/fx/ecb-comparison")).data,
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">FX &amp; ECB rates</h1>
        <p className="text-sm text-slate-500">
          Foreign-currency invoices converted at the European Central Bank reference rate, and the
          EUR markup where a supplier billed at its own rate. Rates: units per 1&nbsp;EUR.
        </p>
      </div>

      <Converter />

      <ComparisonSection data={comparison.data} loading={comparison.isLoading} />

      <div className="card">
        <h2 className="mb-3 text-sm font-semibold text-slate-600">
          ECB reference rates {rates.data?.as_of && `· as of ${shortDate(rates.data.as_of)}`}
        </h2>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4">
          {rates.data?.rates.map((r) => (
            <div key={r.currency} className="flex items-baseline justify-between rounded-lg bg-slate-50 px-3 py-2">
              <span className="font-medium text-slate-700">{r.currency}</span>
              <span className="text-sm text-slate-500">{Number(r.rate).toFixed(4)}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function Converter() {
  const [amount, setAmount] = useState("1000");
  const [from, setFrom] = useState("USD");
  const [to, setTo] = useState("EUR");
  const [result, setResult] = useState<FxConvert | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = useMutation({
    mutationFn: async () =>
      (await api.get<FxConvert>(`/fx/convert?amount=${amount}&from=${from}&to=${to}`)).data,
    onSuccess: (d) => {
      setResult(d);
      setError(null);
    },
    onError: (e) => setError(apiError(e)),
  });

  return (
    <div className="card">
      <h2 className="mb-3 text-sm font-semibold text-slate-600">Converter (at ECB rate)</h2>
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <label className="label">Amount</label>
          <input className="input w-32" value={amount} onChange={(e) => setAmount(e.target.value)} />
        </div>
        <div>
          <label className="label">From</label>
          <input
            className="input w-24 uppercase"
            value={from}
            maxLength={3}
            onChange={(e) => setFrom(e.target.value.toUpperCase())}
          />
        </div>
        <div>
          <label className="label">To</label>
          <input
            className="input w-24 uppercase"
            value={to}
            maxLength={3}
            onChange={(e) => setTo(e.target.value.toUpperCase())}
          />
        </div>
        <button className="btn-primary" onClick={() => run.mutate()} disabled={run.isPending}>
          Convert
        </button>
        {result && (
          <div className="ml-2 text-sm">
            <span className="text-slate-500">
              {result.amount} {result.from_currency} =
            </span>{" "}
            <span className="text-lg font-semibold text-brand-700">
              {Number(result.converted).toLocaleString()} {result.to_currency}
            </span>
            <div className="text-xs text-slate-400">
              rate {Number(result.rate).toFixed(6)} · {shortDate(result.rate_date)}
              {result.approximate && " · approximate"}
            </div>
          </div>
        )}
      </div>
      {error && <div className="mt-2 text-sm text-rose-600">{error}</div>}
    </div>
  );
}

function ComparisonSection({ data, loading }: { data?: FxComparison; loading: boolean }) {
  if (loading) return <div className="card text-sm text-slate-400">Loading…</div>;
  if (!data || data.rows.length === 0) {
    return (
      <div className="card grid h-32 place-items-center text-sm text-slate-400">
        No foreign-currency invoices yet — everything is already in EUR.
      </div>
    );
  }
  const s = data.summary;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <KpiCard
          label="FX markup vs ECB"
          value={money(s.total_markup_eur)}
          accent="rose"
          sub="overpaid on stated rates"
        />
        <KpiCard label="Value at ECB" value={money(s.total_eur_at_ecb)} sub="foreign invoices in EUR" />
        <KpiCard label="Foreign invoices" value={String(s.non_eur_invoices)} />
        <KpiCard label="Currencies" value={s.currencies.join(", ") || "—"} accent="amber" />
      </div>

      <div className="card overflow-x-auto p-0">
        <table className="w-full text-sm">
          <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-3">Invoice</th>
              <th className="px-4 py-3">Date</th>
              <th className="px-4 py-3 text-right">Amount</th>
              <th className="px-4 py-3 text-right">ECB rate</th>
              <th className="px-4 py-3 text-right">Stated rate</th>
              <th className="px-4 py-3 text-right">EUR @ ECB</th>
              <th className="px-4 py-3 text-right">EUR billed</th>
              <th className="px-4 py-3 text-right">Markup</th>
            </tr>
          </thead>
          <tbody>
            {data.rows.map((r) => (
              <tr key={r.invoice_id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50">
                <td className="px-4 py-3">
                  <div className="font-medium">{r.invoice_number}</div>
                  <div className="text-xs text-slate-400">{r.vendor_name}</div>
                </td>
                <td className="px-4 py-3 text-slate-500">{shortDate(r.issue_date)}</td>
                <td className="px-4 py-3 text-right text-slate-600">
                  {Number(r.total).toLocaleString()} {r.currency}
                </td>
                <td className="px-4 py-3 text-right text-slate-500">
                  {r.ecb_rate ? Number(r.ecb_rate).toFixed(4) : "—"}
                </td>
                <td className="px-4 py-3 text-right">
                  {r.stated_rate ? (
                    <span>
                      {Number(r.stated_rate).toFixed(4)}
                      {r.deviation_pct && (
                        <span className={`ml-1 text-xs ${Number(r.deviation_pct) < 0 ? "text-rose-600" : "text-emerald-600"}`}>
                          ({Number(r.deviation_pct) > 0 ? "+" : ""}
                          {r.deviation_pct}%)
                        </span>
                      )}
                    </span>
                  ) : (
                    <span className="text-slate-300">—</span>
                  )}
                </td>
                <td className="px-4 py-3 text-right text-slate-600">{r.eur_at_ecb ? money(r.eur_at_ecb) : "—"}</td>
                <td className="px-4 py-3 text-right text-slate-600">{r.eur_at_stated ? money(r.eur_at_stated) : "—"}</td>
                <td className="px-4 py-3 text-right">
                  {r.markup_eur && Number(r.markup_eur) > 0 ? (
                    <span className="font-medium text-rose-600">{money(r.markup_eur)}</span>
                  ) : (
                    <span className="text-slate-300">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
