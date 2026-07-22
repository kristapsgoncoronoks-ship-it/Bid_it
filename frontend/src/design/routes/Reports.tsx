import { useState } from "react";
import { PageHeader, Card, EmptyState, Button, Badge } from "../../components/ui";

const REPORTS = [
  { key: "vat", name: "VAT return", desc: "Recoverable input VAT by country and period." },
  { key: "ap-aging", name: "Payables ageing", desc: "Outstanding supplier balances by bucket." },
  { key: "ar-aging", name: "Receivables ageing", desc: "Outstanding customer balances by bucket." },
  { key: "spend", name: "Spend by supplier", desc: "Net spend grouped by supplier and category." },
];

/**
 * Reports — a catalog of runnable reports with an explicit empty state instead of
 * pre-rendered fake charts. Selecting a report would open its parameters; here it
 * just marks the selection. (Fixture data.)
 */
export default function Reports() {
  const [selected, setSelected] = useState<string | null>(null);

  return (
    <div className="space-y-6">
      <PageHeader title="Reports" description="Run a report over your validated financial data." />

      <div className="grid gap-4 md:grid-cols-2">
        {REPORTS.map((r) => (
          <Card key={r.key} className={selected === r.key ? "ring-2 ring-brand-300" : ""}>
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="flex items-center gap-2">
                  <h3 className="text-sm font-semibold text-slate-700">{r.name}</h3>
                  {selected === r.key && <Badge tone="brand">selected</Badge>}
                </div>
                <p className="mt-1 text-sm text-slate-500">{r.desc}</p>
              </div>
              <Button size="sm" variant={selected === r.key ? "primary" : "secondary"} onClick={() => setSelected(r.key)}>
                {selected === r.key ? "Selected" : "Choose"}
              </Button>
            </div>
          </Card>
        ))}
      </div>

      <Card title="Output">
        <EmptyState
          title={selected ? "Set parameters to run" : "Choose a report"}
          description={selected ? "Pick a period and scope to generate — output is intentionally not rendered in the fixtures." : "Select a report above to configure and run it."}
        />
      </Card>
    </div>
  );
}
