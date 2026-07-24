import { useMemo, useState } from "react";
import { PageHeader, Button, DataTable, Badge, SearchInput, Tabs, EmptyState, type Column } from "../../components/ui";
import { FIXTURE_CONTACTS, type ContactRow } from "../fixtures";

const TABS = [
  { value: "all", label: "All" },
  { value: "Supplier", label: "Suppliers" },
  { value: "Customer", label: "Customers" },
];

/** Contacts — suppliers and customers, filterable by kind and searchable. (Fixture data.) */
export default function Contacts() {
  const [tab, setTab] = useState("all");
  const [search, setSearch] = useState("");

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    return FIXTURE_CONTACTS.filter(
      (c) => (tab === "all" || c.kind === tab) && (!q || c.name.toLowerCase().includes(q) || c.vat.toLowerCase().includes(q)),
    );
  }, [tab, search]);

  const columns: Column<ContactRow>[] = [
    { key: "name", header: "Name", cell: (c) => <span className="font-medium text-slate-700">{c.name}</span> },
    { key: "kind", header: "Kind", cell: (c) => <Badge tone={c.kind === "Supplier" ? "info" : "brand"}>{c.kind}</Badge> },
    { key: "country", header: "Country", cell: (c) => c.country },
    { key: "vat", header: "VAT number", cell: (c) => <span className="tabular-nums text-slate-500">{c.vat}</span> },
    { key: "email", header: "Email", cell: (c) => <span className="text-slate-500">{c.email}</span> },
  ];

  return (
    <div className="space-y-4">
      <PageHeader title="Contacts" description="Suppliers and customers you transact with." actions={<Button>New contact</Button>} />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Tabs tabs={TABS} value={tab} onChange={setTab} label="Filter contacts by kind" />
        <SearchInput value={search} onChange={setSearch} label="Search contacts" placeholder="Name or VAT…" className="w-64" />
      </div>
      <DataTable
        caption="Contacts"
        columns={columns}
        rows={rows}
        rowKey={(c) => c.id}
        empty={<EmptyState title="No contacts match" description="Adjust the filter or search." />}
      />
    </div>
  );
}
