/**
 * ⚠️ DEVELOPMENT FIXTURES — NOT REAL DATA ⚠️
 *
 * Every value in this file is a hand-written placeholder used ONLY to exercise the
 * design system and shell in the `/design` showcase. It is never fetched, never
 * persisted, and deliberately tiny (a handful of rows) so screens demonstrate
 * structure — headers, tables, states — WITHOUT masquerading as a populated
 * analytics dashboard. The `IS_DEV_FIXTURE` flag and the on-screen banner make
 * this unmistakable. Real pages fetch from the API via react-query.
 */

export const IS_DEV_FIXTURE = true;

export interface FxOrg {
  id: string;
  name: string;
  sub?: string;
}

export const FIXTURE_ORGS: FxOrg[] = [
  { id: "org-north", name: "Northwind Logistics", sub: "Pro plan" },
  { id: "org-baltic", name: "Baltic Freight Group", sub: "Enterprise plan" },
];

export const FIXTURE_ENTITIES: FxOrg[] = [
  { id: "ent-lv", name: "Northwind Latvia SIA", sub: "VAT LV40003…" },
  { id: "ent-lt", name: "Northwind Lietuva UAB", sub: "VAT LT100…" },
  { id: "ent-ee", name: "Northwind Eesti OÜ", sub: "VAT EE1012…" },
];

export const FIXTURE_USER = {
  name: "Dev Fixture",
  email: "dev.fixture@example.test",
  role: "administrator",
};

// --- Supplier (received) invoices ---------------------------------------------
export interface SupplierInvoiceRow {
  id: string;
  supplier: string;
  number: string;
  issued: string; // ISO
  due: string; // ISO
  net: number;
  vat: number;
  currency: string;
  status: string;
}

export const FIXTURE_SUPPLIER_INVOICES: SupplierInvoiceRow[] = [
  { id: "si-1", supplier: "Circle K", number: "CK-88213", issued: "2026-06-30", due: "2026-07-30", net: 4210.5, vat: 884.21, currency: "EUR", status: "pending" },
  { id: "si-2", supplier: "Neste", number: "NE-10233", issued: "2026-06-28", due: "2026-07-12", net: 1980.0, vat: 415.8, currency: "EUR", status: "overdue" },
  { id: "si-3", supplier: "Eurowag", number: "EW-55019", issued: "2026-07-01", due: "2026-08-01", net: 7650.75, vat: 1606.66, currency: "EUR", status: "approved" },
  { id: "si-4", supplier: "DKV Mobility", number: "DKV-4402", issued: "2026-06-15", due: "2026-07-15", net: 3120.0, vat: 655.2, currency: "EUR", status: "paid" },
  { id: "si-5", supplier: "Shell Fleet", number: "SH-77120", issued: "2026-07-03", due: "2026-08-03", net: 540.0, vat: 113.4, currency: "EUR", status: "draft" },
];

// --- Customer (issued) invoices -----------------------------------------------
export interface CustomerInvoiceRow {
  id: string;
  customer: string;
  number: string;
  issued: string;
  due: string;
  total: number;
  currency: string;
  status: string;
}

export const FIXTURE_CUSTOMER_INVOICES: CustomerInvoiceRow[] = [
  { id: "ci-1", customer: "Riga Distribution", number: "INV-2026-0044", issued: "2026-06-20", due: "2026-07-20", total: 12480.0, currency: "EUR", status: "open" },
  { id: "ci-2", customer: "Kaunas Retail", number: "INV-2026-0043", issued: "2026-06-10", due: "2026-06-25", total: 3960.0, currency: "EUR", status: "overdue" },
  { id: "ci-3", customer: "Tallinn Cold Chain", number: "INV-2026-0042", issued: "2026-06-01", due: "2026-07-01", total: 8850.0, currency: "EUR", status: "partial" },
  { id: "ci-4", customer: "Riga Distribution", number: "INV-2026-0039", issued: "2026-05-18", due: "2026-06-18", total: 5400.0, currency: "EUR", status: "paid" },
];

// --- Expenses -----------------------------------------------------------------
export interface ExpenseRow {
  id: string;
  employee: string;
  category: string;
  date: string;
  amount: number;
  currency: string;
  status: string;
  hasReceipt: boolean;
}

export const FIXTURE_EXPENSES: ExpenseRow[] = [
  { id: "ex-1", employee: "A. Ozols", category: "Fuel", date: "2026-07-02", amount: 84.2, currency: "EUR", status: "submitted", hasReceipt: true },
  { id: "ex-2", employee: "M. Kalniņa", category: "Tolls", date: "2026-07-01", amount: 32.5, currency: "EUR", status: "approved", hasReceipt: true },
  { id: "ex-3", employee: "J. Bērziņš", category: "Meals", date: "2026-06-29", amount: 21.0, currency: "EUR", status: "reimbursed", hasReceipt: false },
  { id: "ex-4", employee: "A. Ozols", category: "Parking", date: "2026-06-28", amount: 12.0, currency: "EUR", status: "rejected", hasReceipt: true },
];

// --- Payments -----------------------------------------------------------------
export interface PaymentRow {
  id: string;
  reference: string;
  counterparty: string;
  date: string;
  amount: number;
  currency: string;
  method: string;
  status: string;
}

export const FIXTURE_PAYMENTS: PaymentRow[] = [
  { id: "pay-1", reference: "PMT-5521", counterparty: "Neste", date: "2026-07-05", amount: 2395.8, currency: "EUR", method: "SEPA credit transfer", status: "scheduled" },
  { id: "pay-2", reference: "PMT-5519", counterparty: "DKV Mobility", date: "2026-07-04", amount: 3775.2, currency: "EUR", method: "SEPA credit transfer", status: "completed" },
  { id: "pay-3", reference: "PMT-5514", counterparty: "Circle K", date: "2026-07-03", amount: 5094.71, currency: "EUR", method: "Direct debit", status: "processing" },
  { id: "pay-4", reference: "PMT-5508", counterparty: "Shell Fleet", date: "2026-07-01", amount: 653.4, currency: "EUR", method: "SEPA credit transfer", status: "failed" },
];

// --- Contacts -----------------------------------------------------------------
export interface ContactRow {
  id: string;
  name: string;
  kind: "Supplier" | "Customer";
  country: string;
  vat: string;
  email: string;
}

export const FIXTURE_CONTACTS: ContactRow[] = [
  { id: "c-1", name: "Neste", kind: "Supplier", country: "FI", vat: "FI09876543", email: "billing@neste.example" },
  { id: "c-2", name: "Riga Distribution", kind: "Customer", country: "LV", vat: "LV40003123456", email: "ap@rigadist.example" },
  { id: "c-3", name: "Eurowag", kind: "Supplier", country: "CZ", vat: "CZ12345678", email: "invoices@eurowag.example" },
  { id: "c-4", name: "Tallinn Cold Chain", kind: "Customer", country: "EE", vat: "EE101234567", email: "finance@tcc.example" },
];

// --- Audit-history timeline ---------------------------------------------------
export interface FxTimelineEvent {
  id: string;
  title: string;
  actor: string;
  timestamp: string;
  detail?: string;
  tone: "neutral" | "brand" | "success" | "warning" | "danger" | "info";
}

export const FIXTURE_TIMELINE: FxTimelineEvent[] = [
  { id: "t-1", title: "Payment scheduled", actor: "Dev Fixture", timestamp: "05 Jul 2026, 14:02", detail: "SEPA credit transfer of €2,395.80 to Neste", tone: "info" },
  { id: "t-2", title: "Invoice approved", actor: "M. Kalniņa", timestamp: "03 Jul 2026, 09:41", detail: "Approved for payment after 2-way match", tone: "success" },
  { id: "t-3", title: "VAT flagged for review", actor: "System", timestamp: "01 Jul 2026, 22:15", detail: "Rate 20% differs from supplier default 21%", tone: "warning" },
  { id: "t-4", title: "Invoice received", actor: "Email intake", timestamp: "30 Jun 2026, 06:12", detail: "Parsed from PDF text layer", tone: "neutral" },
];
