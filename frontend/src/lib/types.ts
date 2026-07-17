export type InvoiceStatus = "draft" | "pending" | "paid" | "overdue";

export interface User {
  id: string;
  email: string;
  name: string;
  role: "owner" | "member";
  org_id: string;
}

export interface Organization {
  id: string;
  name: string;
}

export interface AuthResponse {
  token: { access_token: string; token_type: string };
  user: User;
  organization: Organization;
}

export interface Vendor {
  id: string;
  name: string;
  tax_id: string | null;
  country: string | null;
  category: string | null;
}

export interface LineItem {
  id: string;
  description: string;
  category: string;
  quantity: string;
  unit_price: string;
  amount: string;
  tax_rate: string;
}

export interface Invoice {
  id: string;
  vendor_id: string;
  invoice_number: string;
  issue_date: string;
  due_date: string | null;
  currency: string;
  status: InvoiceStatus;
  subtotal: string;
  tax_amount: string;
  total: string;
  source_filename: string | null;
}

export interface InvoiceDetail extends Invoice {
  vendor_name: string;
  notes: string | null;
  line_items: LineItem[];
}

export interface InvoiceList {
  items: Invoice[];
  total: number;
  page: number;
  page_size: number;
}

export interface LineItemInput {
  description: string;
  category: string;
  quantity: string;
  unit_price: string;
  amount?: string | null;
  tax_rate: string;
}

export interface InvoiceCreate {
  vendor_id?: string | null;
  vendor_name?: string | null;
  invoice_number: string;
  issue_date: string;
  due_date?: string | null;
  currency: string;
  status: InvoiceStatus;
  notes?: string | null;
  source_filename?: string | null;
  line_items: LineItemInput[];
}

export interface ParsedDraft {
  draft: InvoiceCreate;
  warnings: string[];
}

export interface Summary {
  total_invoices: number;
  total_spend: string;
  total_tax: string;
  unpaid_amount: string;
  avg_invoice: string;
  vendor_count: number;
  currency: string;
}

export interface TimeBucket {
  period: string;
  total: string;
  invoice_count: number;
}

export interface VendorSpend {
  vendor_id: string;
  vendor_name: string;
  total: string;
  invoice_count: number;
}

export interface CategorySpend {
  category: string;
  total: string;
}

export interface StatusBucket {
  status: string;
  count: number;
  total: string;
}
