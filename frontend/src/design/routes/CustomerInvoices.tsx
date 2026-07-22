import { PageHeader, Button, DataTable, StatusBadge, EmptyState, type Column } from "../../components/ui";
import { money, shortDate } from "../../lib/format";
import { FIXTURE_CUSTOMER_INVOICES, type CustomerInvoiceRow } from "../fixtures";

/** Customer invoices — sales/receivables list. (Fixture data.) */
export default function CustomerInvoices() {
  const columns: Column<CustomerInvoiceRow>[] = [
    { key: "customer", header: "Customer", cell: (r) => <span className="font-medium text-slate-700">{r.customer}</span> },
    { key: "number", header: "Number", cell: (r) => <span className="text-slate-500">{r.number}</span> },
    { key: "issued", header: "Issued", cell: (r) => shortDate(r.issued) },
    { key: "due", header: "Due", cell: (r) => shortDate(r.due) },
    { key: "total", header: "Total", align: "right", cell: (r) => <span className="tabular-nums">{money(r.total, r.currency)}</span> },
    { key: "status", header: "Status", cell: (r) => <StatusBadge status={r.status} /> },
  ];
  return (
    <div className="space-y-4">
      <PageHeader
        title="Customer invoices"
        description="Invoices you've issued and their payment status."
        actions={<Button>New invoice</Button>}
      />
      <DataTable
        caption="Customer invoices"
        columns={columns}
        rows={FIXTURE_CUSTOMER_INVOICES}
        rowKey={(r) => r.id}
        empty={<EmptyState title="No invoices issued" />}
      />
    </div>
  );
}
