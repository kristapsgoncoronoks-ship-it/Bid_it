// A labelled read-only value (definition list item). Was copy-pasted in
// InvoiceDetail, EmailIntake and ExpenseDetail.
export function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-slate-400">{label}</dt>
      <dd className="mt-0.5 font-medium text-slate-700">{value}</dd>
    </div>
  );
}
