import type { ProjectPnl } from "./types";

const SUPPORTED_BASES = new Set([
  "net_eur_live", "net_eur_frozen", "eur_mixed_cost_live", "eur_mixed_cost_frozen",
]);

export function canShowProjectFigures(pnl: ProjectPnl | undefined): boolean {
  if (!pnl || pnl.complete !== true || pnl.basis_version !== 2 || pnl.reporting_currency !== "EUR" || !SUPPORTED_BASES.has(pnl.basis)) return false;
  return [pnl.revenue, pnl.credited, pnl.costs, pnl.invoice_costs, pnl.expense_costs, pnl.manual_costs, pnl.profit]
    .every((value) => typeof value === "string" && /^-?\d+\.\d{2}$/.test(value));
}
