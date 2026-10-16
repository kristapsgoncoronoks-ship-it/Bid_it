import { money } from "../lib/format";
import type { ReceivedVatBreakdown } from "../lib/types";

export function ReceivedVatSummary({ summary, currency }: { summary?: ReceivedVatBreakdown; currency: string }) {
  return <section className="card overflow-x-auto" aria-label="VAT breakdown">
    <h2 className="text-sm font-semibold">VAT breakdown by rate</h2>
    <p className="my-2 text-xs text-slate-500">Saved line calculations in {currency}. Source tax categories, exemptions and reverse-charge treatment are not inferred. Unsaved edits are not included.</p>
    {!summary || !summary.complete ? <p className="text-sm text-amber-700">Incomplete VAT breakdown: some saved calculations are unavailable. Review the source document.</p> : null}
    {summary?.matches_header === false && <p className="text-sm text-amber-700">Line totals differ from invoice header totals. Review the source document before approval.</p>}
    {summary && summary.rates.length > 0 && <table className="mt-3 w-full text-sm">
      <thead><tr className="text-left"><th>Rate</th><th>Net</th><th>VAT</th><th>Gross</th><th>Lines</th></tr></thead>
      <tbody>{summary.rates.map((row) => <tr key={row.tax_rate}>
        <td>{Number(row.tax_rate)}%</td><td>{money(row.net, currency)}</td>
        <td>{row.tax == null ? "Unavailable" : money(row.tax, currency)}</td>
        <td>{row.gross == null ? "Unavailable" : money(row.gross, currency)}</td><td>{row.line_count}</td>
      </tr>)}</tbody>
    </table>}
  </section>;
}
