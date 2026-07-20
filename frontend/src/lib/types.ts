export type InvoiceStatus = "draft" | "pending" | "paid" | "overdue";

export interface User {
  id: string;
  email: string;
  name: string;
  role: "owner" | "member";
  org_id: string;
  is_platform_admin?: boolean;
}

export interface Organization {
  id: string;
  name: string;
  plan?: string;
  status?: string;
}

export interface Member {
  id: string;
  email: string;
  name: string;
  role: "owner" | "member";
  is_active: boolean;
  created_at: string;
}

export interface Invite {
  id: string;
  email: string;
  role: "owner" | "member";
  token: string;
  accepted: boolean;
  created_at: string;
}

export interface PlanInfo {
  key: string;
  name: string;
  seats: number;
  price_eur: number | null;
  modules: string[];
  trial: boolean;
}

export interface BillingInfo {
  plan: PlanInfo;
  status: string;
  seats_used: number;
  seats_limit: number;
  available_plans: PlanInfo[];
}

export interface Tenant {
  id: string;
  name: string;
  plan: string;
  status: string;
  seats_used: number;
  created_at: string;
}

export interface InvitePreview {
  email: string;
  organization_name: string;
  role: "owner" | "member";
}

export const EXPENSE_CATEGORIES = [
  "travel", "meals", "accommodation", "transport", "supplies", "software", "other",
] as const;
export type ExpenseCategory = (typeof EXPENSE_CATEGORIES)[number];

export interface ExpenseItemInput {
  spend_date: string;
  category: ExpenseCategory;
  description: string;
  merchant?: string | null;
  amount: string;
  vat_amount: string;
  payment_method: "personal" | "company_card";
}

export interface ExpenseItem {
  id: string;
  spend_date: string;
  category: string;
  description: string;
  merchant: string | null;
  amount: string;
  vat_amount: string;
  payment_method: string;
  comment: string | null;
  has_receipt: boolean;
}

export interface ExpenseTransaction {
  id: string;
  txn_date: string;
  description: string;
  merchant: string | null;
  amount: string;
  currency: string;
  direction: string;
  source: string;
  status: string;
}

export interface ExpenseComment {
  id: string;
  author_name: string;
  body: string;
  created_at: string;
}

export interface ExpenseReport {
  id: string;
  employee_id: string;
  employee_name: string;
  title: string;
  status: "draft" | "submitted" | "approved" | "rejected" | "reimbursed";
  currency: string;
  total: string;
  vat_total: string;
  total_eur: string | null;
  submitted_at: string | null;
  created_at: string;
}

export interface ExpenseReportDetail extends ExpenseReport {
  note: string | null;
  decided_at: string | null;
  decided_by: string | null;
  decision_note: string | null;
  items: ExpenseItem[];
}

export interface ExpenseSummary {
  my_draft: number;
  my_submitted: number;
  my_reimbursable: string;
  reclaimable_vat: string;
  pending_approvals: number;
  by_category: { category: string; total: string }[];
  currency: string;
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

export type ValidationStatus =
  | "none" | "passed" | "flagged" | "pending" | "approved" | "rejected";

export interface ValidationFinding {
  severity: "info" | "warning" | "error";
  code: string;
  message: string;
  field: string | null;
}

export interface ValidationSettings {
  ai_validation_enabled: boolean;
  human_validation_enabled: boolean;
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
  total_eur?: string | null;
  validation_status: ValidationStatus;
  source_filename: string | null;
}

export interface InvoiceDetail extends Invoice {
  vendor_name: string;
  notes: string | null;
  line_items: LineItem[];
  validation_findings: ValidationFinding[];
  validated_by: string | null;
  validated_at: string | null;
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
  method?: string;
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

export interface ExploreField {
  key: string;
  label: string;
  temporal?: boolean;
  unit?: "money" | "number" | "count";
}

export interface ExploreCatalog {
  dimensions: ExploreField[];
  measures: ExploreField[];
}

export interface ExploreResult {
  measure: { key: string; label: string; unit: "money" | "number" | "count" };
  dimensions: { key: string; label: string; temporal: boolean }[];
  rows: Record<string, string>[];
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

export interface ModuleInfo {
  key: string;
  name: string;
  description: string;
  core: boolean;
  enabled: boolean;
  requires_issuer: boolean;
  ready: boolean;
}

export interface EmailSettings {
  address: string;
  domain: string;
  pending: number;
  total: number;
}

export type InboundStatus = "pending" | "confirmed" | "failed" | "discarded";

export interface InboundInvoice {
  id: string;
  from_addr: string | null;
  subject: string | null;
  received_at: string;
  filename: string;
  content_type: string | null;
  size: number;
  status: InboundStatus;
  method: string | null;
  error: string | null;
  invoice_id: string | null;
  created_at: string;
}

export interface InboundInvoiceDetail extends InboundInvoice {
  draft: ParsedDraft | null;
  has_file: boolean;
}

export interface InboundList {
  items: InboundInvoice[];
  total: number;
}

export interface BudgetTarget {
  category: string;
  monthly_limit: string;
}

export interface BudgetRow {
  category: string;
  budget: string;
  actual: string;
  remaining: string;
  pct: number | null;
  over: boolean;
  untargeted: boolean;
}

export interface BudgetTrendPoint {
  month: string;
  actual: string;
  budget: string;
}

export interface BudgetOverview {
  month: string;
  currency: string;
  total_budget: string;
  total_actual: string;
  total_remaining: string;
  over_budget: boolean;
  rows: BudgetRow[];
  trend: BudgetTrendPoint[];
}

export interface IssuerProfile {
  legal_name: string | null;
  trade_name: string | null;
  vat_number: string | null;
  registration_number: string | null;
  address_line1: string | null;
  address_line2: string | null;
  city: string | null;
  postal_code: string | null;
  country: string | null;
  email: string | null;
  phone: string | null;
  iban: string | null;
  bic: string | null;
  default_currency: string;
  invoice_prefix: string;
  next_number: number;
  payment_terms_days: number;
  notes: string | null;
  is_complete: boolean;
  missing_fields: string[];
  has_logo: boolean;
}

export type VatScheme = "standard" | "reverse_charge" | "intra_eu" | "exempt";

export interface IssuedLineInput {
  description: string;
  quantity: string;
  unit?: string;
  unit_price: string;
  vat_rate: string;
}

export interface VatBucket {
  rate: string;
  base: string;
  vat: string;
}

export interface IssuedInvoice {
  id: string;
  number: string;
  issue_date: string;
  supply_date: string | null;
  due_date: string | null;
  currency: string;
  buyer_name: string;
  buyer_vat_number: string | null;
  vat_scheme: string;
  note: string | null;
  subtotal: string;
  tax_total: string;
  total: string;
}

export interface IssuedInvoiceDetail extends IssuedInvoice {
  lines: {
    position: number;
    description: string;
    quantity: string;
    unit: string;
    unit_price: string;
    vat_rate: string;
    net_amount: string;
  }[];
  vat_breakdown: VatBucket[];
}
