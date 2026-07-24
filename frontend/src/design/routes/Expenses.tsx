import { useState } from "react";
import {
  PageHeader, Button, DataTable, StatusBadge, Modal, CurrencyInput, DateInput, TaxRateInput,
  FileUpload, Select, Textarea, EmptyState, type Column,
} from "../../components/ui";
import { money, shortDate } from "../../lib/format";
import { toast } from "../../components/Toast";
import { FIXTURE_EXPENSES, type ExpenseRow } from "../fixtures";

/**
 * Expenses — the mobile-first submission surface. The "New expense" modal is the
 * canonical form example: a category select, a `CurrencyInput`, `DateInput`,
 * `TaxRateInput` with presets, a `FileUpload` for the receipt, and a note, all with
 * inline validation. Approvals live in the list (status pills). Fixture data.
 */
export default function Expenses() {
  const [open, setOpen] = useState(false);

  const columns: Column<ExpenseRow>[] = [
    { key: "employee", header: "Employee", cell: (r) => <span className="font-medium text-slate-700">{r.employee}</span> },
    { key: "category", header: "Category", cell: (r) => r.category },
    { key: "date", header: "Date", cell: (r) => shortDate(r.date) },
    { key: "amount", header: "Amount", align: "right", cell: (r) => <span className="tabular-nums">{money(r.amount, r.currency)}</span> },
    { key: "receipt", header: "Receipt", cell: (r) => (r.hasReceipt ? "Attached" : <span className="text-slate-400">—</span>) },
    { key: "status", header: "Status", cell: (r) => <StatusBadge status={r.status} /> },
  ];

  return (
    <div className="space-y-4">
      <PageHeader
        title="Expenses"
        description="Submit out-of-pocket costs and approve your team's claims — optimised for phones."
        actions={<Button onClick={() => setOpen(true)}>New expense</Button>}
      />

      <DataTable
        caption="Expense claims"
        columns={columns}
        rows={FIXTURE_EXPENSES}
        rowKey={(r) => r.id}
        empty={<EmptyState title="No expenses yet" description="Submit your first claim." action={<Button size="sm" onClick={() => setOpen(true)}>New expense</Button>} />}
      />

      <NewExpenseModal open={open} onClose={() => setOpen(false)} />
    </div>
  );
}

function NewExpenseModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [category, setCategory] = useState("fuel");
  const [amount, setAmount] = useState("");
  const [date, setDate] = useState("");
  const [vat, setVat] = useState("21");
  const [note, setNote] = useState("");
  const [receipt, setReceipt] = useState<File[]>([]);
  const [touched, setTouched] = useState(false);

  const amountError = touched && !amount ? "Enter an amount" : undefined;
  const dateError = touched && !date ? "Pick the expense date" : undefined;

  function reset() {
    setAmount(""); setDate(""); setVat("21"); setNote(""); setReceipt([]); setTouched(false);
  }

  function submit() {
    setTouched(true);
    if (!amount || !date) return;
    toast.success("Expense submitted (fixture — nothing was saved)");
    reset();
    onClose();
  }

  return (
    <Modal
      open={open}
      onClose={() => { reset(); onClose(); }}
      title="New expense"
      description="Fixture form — inputs validate but nothing is persisted."
      size="md"
      footer={
        <>
          <Button variant="secondary" onClick={() => { reset(); onClose(); }}>Cancel</Button>
          <Button onClick={submit}>Submit</Button>
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Select label="Category" value={category} onChange={(e) => setCategory(e.target.value)} className="sm:col-span-2">
          <option value="fuel">Fuel</option>
          <option value="tolls">Tolls</option>
          <option value="meals">Meals</option>
          <option value="parking">Parking</option>
          <option value="other">Other</option>
        </Select>
        <CurrencyInput label="Amount" value={amount} onValueChange={setAmount} currency="EUR" required error={amountError} />
        <DateInput label="Date" value={date} onValueChange={setDate} required error={dateError} max="2026-12-31" />
        <TaxRateInput label="VAT rate" value={vat} onValueChange={setVat} presets={[0, 9, 12, 21]} />
        <Textarea label="Note" value={note} onChange={(e) => setNote(e.target.value)} placeholder="What was this for?" hint="Optional" />
        <div className="sm:col-span-2">
          <FileUpload label="Receipt" files={receipt} onFilesChange={setReceipt} accept=".pdf,image/*" hint="PDF or image, up to 10 MB" />
        </div>
      </div>
    </Modal>
  );
}
