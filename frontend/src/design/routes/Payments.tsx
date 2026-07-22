import { useState } from "react";
import { PageHeader, Tabs, TabPanel, DataTable, StatusBadge, EmptyState, type Column } from "../../components/ui";
import { money, shortDate } from "../../lib/format";
import { FIXTURE_PAYMENTS, type PaymentRow } from "../fixtures";

const TABS = [
  { value: "all", label: "All" },
  { value: "scheduled", label: "Scheduled" },
  { value: "processing", label: "Processing" },
  { value: "completed", label: "Completed" },
  { value: "failed", label: "Failed" },
];

/** Payments — outgoing settlement runs, filtered by state via WAI-ARIA tabs. (Fixture data.) */
export default function Payments() {
  const [tab, setTab] = useState("all");
  const rows = tab === "all" ? FIXTURE_PAYMENTS : FIXTURE_PAYMENTS.filter((p) => p.status === tab);

  const columns: Column<PaymentRow>[] = [
    { key: "reference", header: "Reference", cell: (r) => <span className="font-medium text-slate-700">{r.reference}</span> },
    { key: "counterparty", header: "Counterparty", cell: (r) => r.counterparty },
    { key: "date", header: "Date", cell: (r) => shortDate(r.date) },
    { key: "method", header: "Method", cell: (r) => <span className="text-slate-500">{r.method}</span> },
    { key: "amount", header: "Amount", align: "right", cell: (r) => <span className="tabular-nums">{money(r.amount, r.currency)}</span> },
    { key: "status", header: "Status", cell: (r) => <StatusBadge status={r.status} /> },
  ];

  return (
    <div className="space-y-4">
      <PageHeader title="Payments" description="Outgoing payment runs to your suppliers." />
      <Tabs tabs={TABS.map((t) => ({ ...t, badge: t.value === "all" ? FIXTURE_PAYMENTS.length : FIXTURE_PAYMENTS.filter((p) => p.status === t.value).length }))} value={tab} onChange={setTab} label="Filter payments by status" idBase="payments" />
      <TabPanel idBase="payments" value={tab}>
        <DataTable
          caption={`Payments — ${tab}`}
          columns={columns}
          rows={rows}
          rowKey={(r) => r.id}
          empty={<EmptyState title="No payments in this state" />}
        />
      </TabPanel>
    </div>
  );
}
