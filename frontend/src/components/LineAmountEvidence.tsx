import { money } from "../lib/format";
import type { ReceivedLineAmounts } from "../lib/types";

export function LineAmountEvidence({ line, currency }: { line: ReceivedLineAmounts; currency: string }) {
  return <div className="mt-1 space-y-1 text-xs font-normal text-slate-500">
    <p>Calculated tax: {line.tax_amount == null ? "unavailable for historical line" : money(line.tax_amount, currency)}
      {" · "}Calculated gross: {line.gross_amount == null ? "unavailable for historical line" : money(line.gross_amount, currency)}</p>
    <p>Captured tax: {line.captured_tax_amount == null ? "not provided" : money(line.captured_tax_amount, currency)}
      {" · "}Captured gross: {line.captured_gross_amount == null ? "not provided" : money(line.captured_gross_amount, currency)}</p>
    {line.source_amounts_match === false && <p className="text-amber-700">Captured amounts differ from calculation; review the source.</p>}
  </div>;
}
