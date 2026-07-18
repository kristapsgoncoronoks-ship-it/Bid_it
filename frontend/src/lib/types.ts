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

export interface SupplierBenchmark {
  vendor_id: string;
  vendor_name: string;
  country: string | null;
  invoice_count: number;
  total_spend: string;
  total_tax: string;
  avg_invoice: string;
  effective_tax_rate: string;
  paid_ratio: string;
  category_count: number;
  first_invoice: string | null;
  last_invoice: string | null;
  spend_share: string;
}

export interface SupplierPricePoint {
  vendor_id: string;
  vendor_name: string;
  unit_price: string;
  quantity: string;
  spend: string;
  deviation_pct: string;
  overspend_vs_cheapest: string;
  is_cheapest: boolean;
}

export interface CategoryBenchmark {
  category: string;
  supplier_count: number;
  total_spend: string;
  total_quantity: string;
  combined_avg_unit: string;
  cheapest_vendor_id: string | null;
  cheapest_vendor_name: string | null;
  cheapest_unit: string;
  savings_opportunity: string;
  suppliers: SupplierPricePoint[];
}

export interface BenchmarkSummary {
  supplier_count: number;
  total_spend: string;
  categories_analyzed: number;
  multi_supplier_categories: number;
  total_savings_opportunity: string;
  currency: string;
}

export interface CombinedBenchmark {
  summary: BenchmarkSummary;
  categories: CategoryBenchmark[];
}

export interface FxRate {
  currency: string;
  rate: string;
  rate_date: string;
  approximate: boolean;
}

export interface FxRates {
  base: string;
  as_of: string | null;
  rates: FxRate[];
}

export interface FxConvert {
  amount: string;
  from_currency: string;
  to_currency: string;
  converted: string;
  rate: string;
  rate_date: string;
  approximate: boolean;
}

export interface FxComparisonRow {
  invoice_id: string;
  invoice_number: string;
  vendor_name: string;
  currency: string;
  issue_date: string;
  total: string;
  ecb_rate: string | null;
  ecb_rate_date: string | null;
  eur_at_ecb: string | null;
  stated_rate: string | null;
  eur_at_stated: string | null;
  markup_eur: string | null;
  deviation_pct: string | null;
}

export interface FxComparison {
  summary: {
    non_eur_invoices: number;
    with_stated_rate: number;
    total_eur_at_ecb: string;
    total_markup_eur: string;
    currencies: string[];
  };
  rows: FxComparisonRow[];
}
