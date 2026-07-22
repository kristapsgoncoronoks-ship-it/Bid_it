import { useState } from "react";
import {
  PageHeader, Card, Tabs, TabPanel, TextInput, Select, TaxRateInput, Button,
  DataTable, StatusBadge, type Column,
} from "../../components/ui";
import { toast } from "../../components/Toast";

const TABS = [
  { value: "general", label: "General" },
  { value: "tax", label: "Tax" },
  { value: "members", label: "Members" },
];

interface MemberRow {
  id: string;
  name: string;
  email: string;
  role: string;
  status: string;
}
const FIXTURE_MEMBERS: MemberRow[] = [
  { id: "m1", name: "Dev Fixture", email: "dev.fixture@example.test", role: "Owner", status: "active" },
  { id: "m2", name: "A. Ozols", email: "a.ozols@example.test", role: "Accountant", status: "active" },
  { id: "m3", name: "M. Kalniņa", email: "m.kalnina@example.test", role: "Approver", status: "suspended" },
];

/** Settings — tabbed configuration demonstrating `Tabs` + `TabPanel` + form inputs. (Fixture data.) */
export default function Settings() {
  const [tab, setTab] = useState("general");
  const [name, setName] = useState("Northwind Latvia SIA");
  const [country, setCountry] = useState("LV");
  const [vat, setVat] = useState("21");

  const memberCols: Column<MemberRow>[] = [
    { key: "name", header: "Name", cell: (m) => <span className="font-medium text-slate-700">{m.name}</span> },
    { key: "email", header: "Email", cell: (m) => <span className="text-slate-500">{m.email}</span> },
    { key: "role", header: "Role", cell: (m) => m.role },
    { key: "status", header: "Status", cell: (m) => <StatusBadge status={m.status} /> },
  ];

  return (
    <div className="space-y-4">
      <PageHeader title="Settings" description="Configure this legal entity's finance workspace." />
      <Tabs tabs={TABS} value={tab} onChange={setTab} label="Settings sections" idBase="settings" />

      {tab === "general" && (
        <TabPanel idBase="settings" value="general">
          <Card title="Company details">
            <div className="grid gap-4 sm:grid-cols-2">
              <TextInput label="Legal name" value={name} onChange={(e) => setName(e.target.value)} required />
              <Select label="Country" value={country} onChange={(e) => setCountry(e.target.value)}>
                <option value="LV">Latvia</option>
                <option value="LT">Lithuania</option>
                <option value="EE">Estonia</option>
              </Select>
              <TextInput label="Registration address" placeholder="Street, city, postcode" className="sm:col-span-2" />
            </div>
            <div className="mt-4">
              <Button onClick={() => toast.success("Saved (fixture — nothing persisted)")}>Save changes</Button>
            </div>
          </Card>
        </TabPanel>
      )}

      {tab === "tax" && (
        <TabPanel idBase="settings" value="tax">
          <Card title="Default tax">
            <div className="grid gap-4 sm:max-w-md">
              <TaxRateInput label="Default VAT rate" value={vat} onValueChange={setVat} presets={[0, 9, 12, 21]} hint="Applied to new lines unless overridden." />
            </div>
          </Card>
        </TabPanel>
      )}

      {tab === "members" && (
        <TabPanel idBase="settings" value="members">
          <DataTable caption="Workspace members" columns={memberCols} rows={FIXTURE_MEMBERS} rowKey={(m) => m.id} />
        </TabPanel>
      )}
    </div>
  );
}
