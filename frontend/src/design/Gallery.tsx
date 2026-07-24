import { useState, type ReactNode } from "react";
import {
  Button, Badge, Dot, StatusBadge, Card, StatCard, PageHeader, Breadcrumbs, Tabs, TabPanel,
  DataTable, Pagination, SearchInput, FilterBar, FilterSelect, FilterChip, Timeline,
  TextInput, Select, Textarea, CurrencyInput, DateInput, TaxRateInput, FileUpload,
  Modal, Drawer, ConfirmDialog, EmptyState, ErrorState, Spinner, Skeleton, SkeletonText,
  useSort, type Column,
} from "../components/ui";
import { toast } from "../components/Toast";
import { DevBanner } from "./DevBanner";

/** A labelled story block with a demo canvas. `id` anchors it for nav + VR tests. */
function Section({ id, title, children, note }: { id: string; title: string; note?: string; children: ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${id}-h`} className="scroll-mt-4">
      <div className="mb-3">
        <h2 id={`${id}-h`} className="text-lg font-semibold tracking-tight text-slate-800">
          {title}
        </h2>
        {note && <p className="mt-0.5 text-sm text-slate-500">{note}</p>}
      </div>
      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-xs">{children}</div>
    </section>
  );
}

function Row({ children }: { children: ReactNode }) {
  return <div className="flex flex-wrap items-center gap-3">{children}</div>;
}

const SECTIONS: { id: string; title: string }[] = [
  { id: "buttons", title: "Buttons" },
  { id: "badges", title: "Badges & status" },
  { id: "cards", title: "Cards & stat tiles" },
  { id: "headers", title: "Page header & breadcrumbs" },
  { id: "tabs", title: "Tabs" },
  { id: "table", title: "Data table (sortable)" },
  { id: "pagination", title: "Pagination" },
  { id: "filters", title: "Search & filter bar" },
  { id: "forms", title: "Forms & validation" },
  { id: "finance-inputs", title: "Currency · date · tax-rate" },
  { id: "upload", title: "File upload" },
  { id: "overlays", title: "Modal · drawer · confirm" },
  { id: "toast", title: "Toasts" },
  { id: "timeline", title: "Audit timeline" },
  { id: "states", title: "Empty · error · loading" },
];

interface DemoRow { id: string; name: string; amount: number; date: string; }
const DEMO: DemoRow[] = [
  { id: "1", name: "Circle K", amount: 5094.71, date: "2026-06-30" },
  { id: "2", name: "Neste", amount: 2395.8, date: "2026-06-28" },
  { id: "3", name: "Eurowag", amount: 9257.41, date: "2026-07-01" },
];

/**
 * The living component gallery — one example ("story") per reusable primitive,
 * rendered with fixtures. Doubles as the visual-regression target: every state
 * that matters is on screen and deterministic. No Storybook dependency; this is an
 * in-app route so the components render in their real Tailwind/runtime environment.
 */
export default function Gallery() {
  const [tab, setTab] = useState("overview");
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const [amount, setAmount] = useState("1250.00");
  const [date, setDate] = useState("2026-07-15");
  const [vat, setVat] = useState("21");
  const [files, setFiles] = useState<File[]>([]);
  const [name, setName] = useState("");
  const [modal, setModal] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const [confirm, setConfirm] = useState(false);

  const { sort, toggle, sorted } = useSort<DemoRow>(
    DEMO,
    { name: (r) => r.name, amount: (r) => r.amount, date: (r) => r.date },
    { key: "amount", dir: "desc" },
  );
  const cols: Column<DemoRow>[] = [
    { key: "name", header: "Supplier", sortable: true, cell: (r) => r.name },
    { key: "date", header: "Date", sortable: true, cell: (r) => r.date },
    { key: "amount", header: "Amount", sortable: true, align: "right", cell: (r) => <span className="tabular-nums">€{r.amount.toFixed(2)}</span> },
  ];

  return (
    <div className="min-h-screen bg-slate-50">
      <DevBanner />
      <div className="mx-auto grid max-w-6xl gap-8 px-4 py-8 lg:grid-cols-[12rem_1fr]">
        {/* In-page nav */}
        <nav aria-label="Components" className="hidden lg:block">
          <div className="sticky top-8">
            <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">Components</p>
            <ul className="space-y-1 text-sm">
              {SECTIONS.map((s) => (
                <li key={s.id}>
                  <a href={`#${s.id}`} className="block rounded-sm px-2 py-1 text-slate-600 hover:bg-white hover:text-brand-700">
                    {s.title}
                  </a>
                </li>
              ))}
            </ul>
          </div>
        </nav>

        <div className="min-w-0 space-y-10">
          <header>
            <h1 className="text-2xl font-bold tracking-tight text-slate-900">InvoiceIQ design system</h1>
            <p className="mt-1 text-slate-500">
              Every reusable primitive, with live examples. Keyboard-navigable and screen-reader labelled by construction.
            </p>
          </header>

          <Section id="buttons" title="Buttons" note="One primitive, semantic variants + sizes + loading.">
            <Row>
              <Button>Primary</Button>
              <Button variant="secondary">Secondary</Button>
              <Button variant="ghost">Ghost</Button>
              <Button variant="danger">Danger</Button>
              <Button variant="subtle">Subtle</Button>
              <Button loading>Saving…</Button>
              <Button disabled>Disabled</Button>
              <Button size="sm">Small</Button>
            </Row>
          </Section>

          <Section id="badges" title="Badges & status" note="Fixed tone palette; StatusBadge maps a status string to tone + label.">
            <Row>
              <Badge tone="neutral">neutral</Badge>
              <Badge tone="brand">brand</Badge>
              <Badge tone="success">success</Badge>
              <Badge tone="warning">warning</Badge>
              <Badge tone="danger">danger</Badge>
              <Badge tone="info">info</Badge>
              <span className="inline-flex items-center gap-1.5 text-sm text-slate-600"><Dot tone="success" /> live</span>
            </Row>
            <Row>
              {["draft", "pending", "approved", "paid", "overdue", "partial", "reimbursed", "failed"].map((s) => (
                <StatusBadge key={s} status={s} />
              ))}
            </Row>
          </Section>

          <Section id="cards" title="Cards & stat tiles">
            <div className="grid gap-4 sm:grid-cols-2">
              <Card title="Card with header" actions={<Button size="sm" variant="ghost">Action</Button>}>
                <p className="text-sm text-slate-500">Surface container for grouped content.</p>
              </Card>
              <StatCard label="Fixture metric" value="€12,480" sub="clearly-marked placeholder" accent="brand" />
            </div>
          </Section>

          <Section id="headers" title="Page header & breadcrumbs">
            <Breadcrumbs items={[{ label: "Workspace", to: "#" }, { label: "Payables", to: "#" }, { label: "Supplier invoices" }]} />
            <div className="mt-3 rounded-lg border border-dashed border-slate-200 p-3">
              <PageHeader title="Supplier invoices" description="One place page titles + actions are composed." actions={<Button size="sm">New bill</Button>} meta={<Badge tone="info">42 open</Badge>} />
            </div>
          </Section>

          <Section id="tabs" title="Tabs" note="WAI-ARIA tablist: arrow keys, roving tabindex, linked panels.">
            <Tabs
              idBase="gallery-tabs"
              label="Example tabs"
              value={tab}
              onChange={setTab}
              tabs={[{ value: "overview", label: "Overview" }, { value: "lines", label: "Lines", badge: 3 }, { value: "history", label: "History" }, { value: "disabled", label: "Disabled", disabled: true }]}
            />
            <TabPanel idBase="gallery-tabs" value={tab}>
              <p className="text-sm text-slate-500">Active panel: <strong>{tab}</strong></p>
            </TabPanel>
          </Section>

          <Section id="table" title="Data table (sortable)" note="Click a header — aria-sort updates; keyboard-activatable.">
            <DataTable caption="Demo rows" columns={cols} rows={sorted} rowKey={(r) => r.id} sort={sort} onSortChange={toggle} />
          </Section>

          <Section id="pagination" title="Pagination">
            <Pagination page={page} total={42} pageSize={10} onPageChange={setPage} />
          </Section>

          <Section id="filters" title="Search & filter bar">
            <FilterBar>
              <SearchInput value={search} onChange={setSearch} label="Search" placeholder="Search…" className="w-56" />
              <FilterSelect label="Status" value={filter} onChange={setFilter} options={[{ value: "all", label: "All" }, { value: "open", label: "Open" }, { value: "paid", label: "Paid" }]} />
              {filter !== "all" && <div className="flex items-end pb-1"><FilterChip label={`Status: ${filter}`} onRemove={() => setFilter("all")} /></div>}
            </FilterBar>
          </Section>

          <Section id="forms" title="Forms & validation" note="FormField wires label, hint, aria-describedby, aria-invalid, role=alert errors.">
            <div className="grid gap-4 sm:grid-cols-2">
              <TextInput label="Full name" value={name} onChange={(e) => setName(e.target.value)} hint="As it appears on the invoice" required />
              <TextInput label="Email" type="email" placeholder="name@company.com" error={name === "" ? undefined : "Example error message"} />
              <Select label="Country"><option>Latvia</option><option>Lithuania</option></Select>
              <Textarea label="Notes" placeholder="Optional context" />
            </div>
          </Section>

          <Section id="finance-inputs" title="Currency · date · tax-rate" note="Domain inputs: sanitised money, native date, VAT presets.">
            <div className="grid gap-4 sm:grid-cols-3">
              <CurrencyInput label="Amount" value={amount} onValueChange={setAmount} currency="EUR" />
              <DateInput label="Invoice date" value={date} onValueChange={setDate} />
              <TaxRateInput label="VAT rate" value={vat} onValueChange={setVat} presets={[0, 9, 12, 21]} />
            </div>
          </Section>

          <Section id="upload" title="File upload" note="Keyboard-openable dropzone + drag-and-drop + file list.">
            <FileUpload label="Receipt" files={files} onFilesChange={setFiles} accept=".pdf,image/*" hint="PDF or image, up to 10 MB" />
          </Section>

          <Section id="overlays" title="Modal · drawer · confirm" note="Focus-trapped, Escape to close, focus restored, scroll-locked.">
            <Row>
              <Button onClick={() => setModal(true)}>Open modal</Button>
              <Button variant="secondary" onClick={() => setDrawer(true)}>Open drawer</Button>
              <Button variant="danger" onClick={() => setConfirm(true)}>Delete…</Button>
            </Row>
            <Modal open={modal} onClose={() => setModal(false)} title="Example modal" description="A focus-trapped dialog." footer={<><Button variant="secondary" onClick={() => setModal(false)}>Cancel</Button><Button onClick={() => setModal(false)}>Save</Button></>}>
              Body content goes here. Tab stays inside; Escape closes.
            </Modal>
            <Drawer open={drawer} onClose={() => setDrawer(false)} title="Example drawer" description="A side panel for record detail.">
              <p>Slides in from the right; same a11y contract as the modal.</p>
            </Drawer>
            <ConfirmDialog open={confirm} onClose={() => setConfirm(false)} onConfirm={() => { setConfirm(false); toast.success("Confirmed (demo)"); }} title="Delete this item?" tone="danger" confirmLabel="Delete">
              This cannot be undone.
            </ConfirmDialog>
          </Section>

          <Section id="toast" title="Toasts" note="role=alert; auto-dismiss; click to dismiss.">
            <Row>
              <Button variant="secondary" onClick={() => toast.success("Saved successfully")}>Success toast</Button>
              <Button variant="secondary" onClick={() => toast.error("Something went wrong")}>Error toast</Button>
            </Row>
          </Section>

          <Section id="timeline" title="Audit timeline">
            <Timeline events={[
              { id: "1", title: "Payment scheduled", actor: "Dev Fixture", timestamp: "05 Jul, 14:02", tone: "info", detail: "SEPA transfer €2,395.80" },
              { id: "2", title: "Invoice approved", actor: "M. Kalniņa", timestamp: "03 Jul, 09:41", tone: "success" },
              { id: "3", title: "VAT flagged", actor: "System", timestamp: "01 Jul, 22:15", tone: "warning", detail: "Rate differs from default" },
            ]} />
          </Section>

          <Section id="states" title="Empty · error · loading">
            <div className="grid gap-4 md:grid-cols-3">
              <div className="rounded-lg border border-slate-200"><EmptyState title="No invoices yet" description="They'll show up here." action={<Button size="sm">New</Button>} /></div>
              <div className="rounded-lg border border-slate-200"><ErrorState onRetry={() => toast.error("Retried (demo)")} /></div>
              <div className="rounded-lg border border-slate-200 p-5">
                <div className="mb-3 flex items-center gap-2 text-sm text-slate-500"><Spinner /> Loading…</div>
                <SkeletonText lines={3} />
                <div className="mt-3 flex gap-2"><Skeleton className="h-8 w-20" /><Skeleton className="h-8 w-20" /></div>
              </div>
            </div>
          </Section>
        </div>
      </div>
    </div>
  );
}
