import { useMemo, useState } from "react";
import {
  PageHeader, Button, DataTable, StatusBadge, Pagination, FilterBar, FilterSelect, FilterChip,
  SearchInput, Drawer, ConfirmDialog, Timeline, EmptyState, useSort,
  type Column,
} from "../../components/ui";
import { money, shortDate } from "../../lib/format";
import { toast } from "../../components/Toast";
import { FIXTURE_SUPPLIER_INVOICES, FIXTURE_TIMELINE, type SupplierInvoiceRow } from "../fixtures";

const STATUS_OPTIONS = [
  { value: "all", label: "All statuses" },
  { value: "draft", label: "Draft" },
  { value: "pending", label: "Pending" },
  { value: "approved", label: "Approved" },
  { value: "paid", label: "Paid" },
  { value: "overdue", label: "Overdue" },
];

const PAGE_SIZE = 3;

/**
 * Supplier invoices — the reference list page. Composes the filter bar (search +
 * status), a sortable/paginated `DataTable`, a row-click detail `Drawer` with an
 * audit timeline, and a `ConfirmDialog` for the approve action. Everything here is
 * a fixture; a real page swaps the array for a react-query result.
 */
export default function SupplierInvoices() {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<SupplierInvoiceRow | null>(null);
  const [confirming, setConfirming] = useState(false);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return FIXTURE_SUPPLIER_INVOICES.filter((r) => {
      const matchesQ = !q || r.supplier.toLowerCase().includes(q) || r.number.toLowerCase().includes(q);
      const matchesS = status === "all" || r.status === status;
      return matchesQ && matchesS;
    });
  }, [search, status]);

  const { sort, toggle, sorted } = useSort<SupplierInvoiceRow>(
    filtered,
    {
      supplier: (r) => r.supplier,
      issued: (r) => r.issued,
      due: (r) => r.due,
      gross: (r) => r.net + r.vat,
    },
    { key: "due", dir: "asc" },
  );

  const total = sorted?.length ?? 0;
  const pageRows = (sorted ?? []).slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  const columns: Column<SupplierInvoiceRow>[] = [
    { key: "supplier", header: "Supplier", sortable: true, cell: (r) => <span className="font-medium text-slate-700">{r.supplier}</span> },
    { key: "number", header: "Number", cell: (r) => <span className="text-slate-500">{r.number}</span> },
    { key: "issued", header: "Issued", sortable: true, cell: (r) => shortDate(r.issued) },
    { key: "due", header: "Due", sortable: true, cell: (r) => shortDate(r.due) },
    { key: "gross", header: "Gross", sortable: true, align: "right", cell: (r) => <span className="tabular-nums">{money(r.net + r.vat, r.currency)}</span> },
    { key: "status", header: "Status", cell: (r) => <StatusBadge status={r.status} /> },
  ];

  return (
    <div className="space-y-4">
      <PageHeader
        title="Supplier invoices"
        description="Bills received from your fuel, toll and service suppliers."
        actions={
          <>
            <Button variant="secondary">Import</Button>
            <Button>New bill</Button>
          </>
        }
      />

      <FilterBar>
        <SearchInput value={search} onChange={(v) => { setSearch(v); setPage(1); }} label="Search invoices" placeholder="Supplier or number…" className="w-64" />
        <FilterSelect label="Status" value={status} onChange={(v) => { setStatus(v); setPage(1); }} options={STATUS_OPTIONS} />
        {status !== "all" && (
          <div className="flex items-end pb-1">
            <FilterChip label={`Status: ${status}`} onRemove={() => setStatus("all")} />
          </div>
        )}
      </FilterBar>

      <DataTable
        caption="Supplier invoices"
        columns={columns}
        rows={pageRows}
        rowKey={(r) => r.id}
        sort={sort}
        onSortChange={toggle}
        onRowClick={setSelected}
        empty={<EmptyState title="No invoices match" description="Try clearing the search or status filter." />}
      />

      <Pagination page={page} total={total} pageSize={PAGE_SIZE} onPageChange={setPage} />

      <Drawer
        open={!!selected}
        onClose={() => setSelected(null)}
        title={selected ? selected.supplier : ""}
        description={selected?.number}
        size="md"
        footer={
          selected && (
            <>
              <Button variant="secondary" onClick={() => setSelected(null)}>Close</Button>
              <Button onClick={() => setConfirming(true)}>Approve</Button>
            </>
          )
        }
      >
        {selected && (
          <div className="space-y-5">
            <dl className="grid grid-cols-2 gap-4">
              <Detail label="Issued" value={shortDate(selected.issued)} />
              <Detail label="Due" value={shortDate(selected.due)} />
              <Detail label="Net" value={money(selected.net, selected.currency)} />
              <Detail label="VAT" value={money(selected.vat, selected.currency)} />
              <Detail label="Gross" value={money(selected.net + selected.vat, selected.currency)} />
              <Detail label="Status" value={<StatusBadge status={selected.status} />} />
            </dl>
            <div>
              <h3 className="mb-2 text-sm font-semibold text-slate-600">History</h3>
              <Timeline events={FIXTURE_TIMELINE.map((e) => ({ id: e.id, title: e.title, actor: e.actor, timestamp: e.timestamp, detail: e.detail, tone: e.tone }))} />
            </div>
          </div>
        )}
      </Drawer>

      <ConfirmDialog
        open={confirming}
        onClose={() => setConfirming(false)}
        onConfirm={() => {
          setConfirming(false);
          setSelected(null);
          toast.success("Invoice approved (fixture — nothing was changed)");
        }}
        title="Approve this invoice?"
        confirmLabel="Approve"
      >
        Approving marks the bill ready for payment. In the fixtures this is a no-op.
      </ConfirmDialog>
    </div>
  );
}

function Detail({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-slate-400">{label}</dt>
      <dd className="mt-0.5 font-medium text-slate-700">{value}</dd>
    </div>
  );
}
