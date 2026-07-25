export type InvoiceStatus = "draft" | "pending" | "paid" | "overdue";

// Shape of every paginated list endpoint ({ items, total }).
export interface Paginated<T> {
  items: T[];
  total: number;
}

// The four user groups (low → high privilege).
export type UserRoleName = "user_free" | "user" | "admin" | "owner";

export interface RolePolicy {
  role: UserRoleName;
  label: string;
  paid: boolean;
  description: string;
  monthly_invoice_limit: number; // 0 = unlimited
  monthly_upload_limit: number;
}

export interface Usage {
  role: UserRoleName;
  invoices_used: number;
  invoice_limit: number;
  invoices_remaining: number | null;
  unlimited: boolean;
}

// Immutable, hash-chained audit trail (sysadmin-only).
export interface AuditEvent {
  id: string;
  seq: number;
  actor_email: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  meta: Record<string, unknown> | null;
  at: string;
}

export interface ChainStatus {
  ok: boolean;
  events: number;
  broken_at_seq: number | null;
  detail: string | null;
}

export interface User {
  id: string;
  email: string;
  name: string;
  role: UserRoleName;
  org_id: string;
  is_platform_admin?: boolean;
  is_expense_approver?: boolean;
  iban?: string | null;
  bic?: string | null;
}

export interface Organization {
  id: string;
  name: string;
  plan?: string;
  status?: string;
  region?: string;
}

export interface Member {
  id: string;
  email: string;
  name: string;
  role: UserRoleName;
  is_active: boolean;
  is_expense_approver: boolean;
  created_at: string;
}

export interface Invite {
  id: string;
  email: string;
  role: UserRoleName;
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
  billing_enabled: boolean;
  billing_provider: string;
  has_subscription: boolean;
}

export interface RetentionCategory {
  key: string;
  label: string;
  retain_days: number | null;
  purgeable_now: number;
}

export interface LegalHold {
  id: string;
  reason: string;
  active: boolean;
  placed_by: string | null;
  released_by: string | null;
  released_at: string | null;
  created_at: string;
}

export interface RetentionInfo {
  categories: RetentionCategory[];
  on_hold: boolean;
  holds: LegalHold[];
}

export interface ErasureLocation {
  key: string;
  label: string;
  matched: number;
  action: "erase" | "retain" | "blocked";
  reason: string | null;
}

export interface ErasureReport {
  email: string;
  on_hold: boolean;
  executed: boolean;
  locations: ErasureLocation[];
}

export interface SsoConnection {
  id: string;
  slug: string;
  protocol: string;
  enabled: boolean;
  issuer: string | null;
  client_id: string | null;
  allowed_domain: string | null;
  jit_enabled: boolean;
  default_role: string;
  has_client_secret: boolean;
  scim_enabled: boolean;
  login_url: string | null;
  scim_base_url: string | null;
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
  role: UserRoleName;
}

export const EXPENSE_CATEGORIES = [
  "travel", "meals", "accommodation", "transport", "supplies", "software", "other",
] as const;
export type ExpenseCategory = (typeof EXPENSE_CATEGORIES)[number];

export type ExpenseType = "standard" | "mileage" | "per_diem";

export interface ExpenseItemInput {
  spend_date: string;
  category: ExpenseCategory;
  description: string;
  merchant?: string | null;
  amount: string;
  vat_amount: string;
  reclaimable_tax?: boolean;
  payment_method: "personal" | "company_card";
  customer_billable?: boolean;
  billable_customer?: string | null;
  comment?: string;   // business purpose
  missing_receipt_declaration?: string | null;
  currency?: string | null;
  original_amount?: string | null;
  fx_rate?: string | null;
  fx_source?: string | null;
  expense_type?: ExpenseType;
  mileage_distance?: string | null;
  mileage_rate?: string | null;
  mileage_unit?: "km" | "mi" | null;
  per_diem_days?: string | null;
  per_diem_rate?: string | null;
}

export interface ExpenseItem {
  id: string;
  spend_date: string;
  category: string;
  description: string;
  merchant: string | null;
  amount: string;
  currency: string | null;
  original_amount: string | null;
  fx_rate: string | null;
  fx_source: string | null;
  vat_amount: string;
  reclaimable_tax: boolean;
  payment_method: string;
  customer_billable: boolean;
  billable_customer: string | null;
  comment: string | null;
  missing_receipt_declaration: string | null;
  expense_type: string;
  mileage_distance: string | null;
  mileage_rate: string | null;
  mileage_unit: string | null;
  per_diem_days: string | null;
  per_diem_rate: string | null;
  has_receipt: boolean;
  verified: boolean;
  bank_reference: string | null;
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
  status:
    | "draft"
    | "submitted"
    | "partially_approved"
    | "approved"
    | "rejected"
    | "returned"
    | "marked_for_reimbursement"
    | "reimbursed";
  currency: string;
  total: string;
  vat_total: string;
  total_eur: string | null;
  submitted_at: string | null;
  created_at: string;
}

export interface PolicyViolation {
  code: string;
  message: string;
  severity: "warn" | "block";
  item_id: string | null;
  category: string | null;
  amount: string | null;
  limit: string | null;
}

export const EXPENSE_POLICY_RULES = [
  "over_item_max",
  "over_category_cap",
  "missing_receipt",
  "out_of_policy_category",
  "unsupported_currency",
  "mileage_rate",
  "missing_business_purpose",
  "late_submission",
  "weekend_spend",
  "duplicate_receipt",
  "duplicate_amount_date_merchant",
] as const;

export interface ExpensePolicy {
  active: boolean;
  max_item_amount: string | null;
  receipt_required_over: string | null;
  category_caps: Record<string, string>;
  allowed_categories: string[];
  allowed_currencies: string[];
  warn_weekend: boolean;
  duplicate_detection: boolean;
  mileage_rate: string | null;
  mileage_rate_tolerance: string | null;
  require_purpose_over: string | null;
  late_submission_days: number | null;
  blocking_rules: string[];
  version: number;
}

export interface ApprovalStep {
  id: string;
  seq: number;
  kind: string;
  approver_id: string | null;
  approver_email: string | null;
  status: string;
  decided_by_email: string | null;
  decided_at: string | null;
  note: string | null;
}

export interface ExpenseReportDetail extends ExpenseReport {
  note: string | null;
  decided_at: string | null;
  decided_by: string | null;
  decision_note: string | null;
  items: ExpenseItem[];
  policy_violations?: PolicyViolation[];
  approval_steps?: ApprovalStep[];
}

export interface CustomerContact {
  id?: string;
  name: string;
  email?: string | null;
  phone?: string | null;
  role?: string | null;
}

export interface Customer {
  id: string;
  name: string;
  legal_name?: string | null;
  vat_number?: string | null;
  registration_number?: string | null;
  email?: string | null;
  phone?: string | null;
  address_line1?: string | null;
  address_line2?: string | null;
  city?: string | null;
  postal_code?: string | null;
  country?: string | null;
  ship_address_line1?: string | null;
  ship_city?: string | null;
  ship_postal_code?: string | null;
  ship_country?: string | null;
  payment_terms_days?: number | null;
  default_currency?: string | null;
  notes?: string | null;
  is_active: boolean;
  contacts: CustomerContact[];
}

export interface ExpenseApprovalPolicy {
  id: string;
  name: string;
  active: boolean;
  priority: number;
  min_amount: string | null;
  approver_ids: string[];
  finance_final: boolean;
  finance_approver_id: string | null;
  version: number;
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

export interface VendorChangeRequest {
  id: string;
  vendor_id: string;
  vendor_name?: string | null;
  field: string;
  old_value: string | null;
  new_value: string | null;
  status: "pending" | "approved" | "rejected";
  requested_by: string;
  requested_by_email: string | null;
  requested_at: string;
  decided_by: string | null;
  decided_by_email: string | null;
  decided_at: string | null;
  decision_note: string | null;
  source_document_id: string | null;
}

export interface Vendor {
  id: string;
  name: string;
  tax_id: string | null;
  country: string | null;
  category: string | null;
  iban?: string | null;
  bic?: string | null;
  status?: "active" | "provisional";
  version?: number;
  pending_changes?: VendorChangeRequest[];
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

// Cost-allocation dimensions shared by invoices and expense items.
export interface Dimensions {
  cost_center: string | null;
  department: string | null;
  project: string | null;
  vehicle: string | null;
  property_ref: string | null;
}

export const DIMENSION_LABELS: Record<keyof Dimensions, string> = {
  cost_center: "Cost center",
  department: "Department",
  project: "Project",
  vehicle: "Vehicle",
  property_ref: "Property",
};

export interface Invoice extends Dimensions {
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
  // AP settlement (Phase 13).
  workflow_state: string | null;
  amount_paid: string;
  paid_date: string | null;
  outstanding: string;
  payment_status: "paid" | "partial" | "open" | "overdue";
}

export interface SupplierPayment {
  id: string;
  invoice_id: string;
  amount: string;
  paid_on: string;
  method: string;
  reference: string | null;
  note: string | null;
  created_at: string;
}

// Supplier payment runs (Phase 14).
export interface RunInvoice {
  id: string;
  invoice_number: string;
  vendor_name: string | null;
  total: string;
  currency: string;
  total_eur: string | null;
  workflow_state: string;
}

export interface PaymentRun {
  id: string;
  reference: string | null;
  method: string;
  status: "open" | "paid" | "cancelled";
  note: string | null;
  total_eur: string;
  paid_at: string | null;
  created_by: string | null;
  version: number;
  created_at: string;
  invoice_count: number;
}

export interface PaymentRunDetail extends PaymentRun {
  invoices: RunInvoice[];
}

// Cash-flow trend (Phase 19).
export interface CashFlowPoint {
  period: string;
  inflow: string;
  outflow: string;
  net: string;
}

// AP aging worklist (Phase 16b).
export interface ApWorklistItem {
  id: string;
  invoice_number: string;
  vendor_name: string | null;
  due_date: string | null;
  currency: string;
  total: string;
  outstanding: string;
  status: "open" | "partial" | "paid" | "overdue";
  days_overdue: number;
  bucket: string;
}

export interface ApAging {
  // Amounts are single-currency (`currency`) — currencies that could not be
  // folded in are listed in `other_currencies`, never silently summed (WO-8).
  currency: string;
  due_soon_count: number;
  due_soon_amount: string;
  overdue_count: number;
  overdue_amount: string;
  other_currencies: string[];
  items: ApWorklistItem[];
}

// Dunning ladder (Phase 16).
export interface DunningLevel {
  level: number;
  days_overdue: number;
  tone: "reminder" | "firm" | "final";
  active: boolean;
}

export interface DunningPolicy {
  is_default: boolean;
  levels: DunningLevel[];
}

// Cash-position dashboard (Phase 15).
export interface CashPosition {
  currency: string;
  receivables: {
    currency: string;
    outstanding: string;
    overdue: string;
    avg_days_to_pay: number | null;
    aging: { label: string; count: number; outstanding: string }[];
  };
  payables: {
    outstanding: string;
    overdue: string;
    count: number;
    scheduled: number;
    in_run: number;
  };
  reconciliation: {
    unmatched: number;
    matched: number;
    ignored: number;
    unmatched_amount: string;
  };
  net_position: string;
}

export interface InvoiceList {
  items: Invoice[];
  total: number;
  page: number;
  page_size: number;
}

export interface DimensionSpend {
  value: string;
  total: string;
  invoice_count: number;
}

export interface DimensionBreakdown {
  dimension: string;
  label: string;
  rows: DimensionSpend[];
  total: string;
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

// Async direct-upload capture (Stage B): the parse/OCR runs on the worker tier.
export interface UploadAccepted {
  extraction_run_id: string;
  status: string; // queued | running
}

export interface ExtractionResult {
  extraction_run_id: string;
  status: "queued" | "running" | "parsed" | "failed";
  method?: string | null;
  draft?: ParsedDraft | null;
  error?: string | null;
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

export interface FxCurrency {
  code: string;
  name: string;
  ecb: boolean;
  rate: string | null;
  rate_date: string | null;
  indicative: boolean;
}

export interface FxCurrencies {
  base: string;
  region: string;
  currencies: FxCurrency[];
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

export type InboundStatus = "pending" | "confirmed" | "failed" | "discarded" | "rejected";

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
  id: string;
  name: string;
  is_default: boolean;
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
  credit_note_prefix: string;
  next_number: number;
  payment_terms_days: number;
  default_penalty_rate: string | null;
  payment_instructions: string | null;
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
  discount_percent?: string;
  vat_rate: string;
}

export interface IssuedAttachment {
  id: string;
  filename: string;
  mime: string | null;
  size: number;
  note: string | null;
  uploaded_by_email: string | null;
  created_at: string;
}

export interface VatBucket {
  rate: string;
  base: string;
  vat: string;
}

export type IssuedStatus =
  | "draft"
  | "approved"
  | "sent"
  | "viewed"
  | "paid"
  | "partial"
  | "open"
  | "overdue"
  | "credited"
  | "credit_note"
  | "disputed"
  | "written_off"
  | "void";

export type IssuedLifecycle =
  | "draft"
  | "approved"
  | "issued"
  | "disputed"
  | "written_off"
  | "cancelled";

// --- Partners (counterparties) with a pre-invoicing document workflow ---
export type PartnerDocKind = "contract" | "acceptance_act";

export interface PartnerDocument {
  id: string;
  kind: PartnerDocKind;
  title: string;
  reference: string | null;
  status: "draft" | "signed";
  signed_by: string | null;
  signed_date: string | null;
  note: string | null;
}

export interface PartnerReadiness {
  ready: boolean;
  required: string[];
  signed: string[];
  missing: string[];
}

export interface Partner {
  id: string;
  name: string;
  email: string | null;
  vat_number: string | null;
  address_line1: string | null;
  city: string | null;
  postal_code: string | null;
  country: string | null;
  requires_contract: boolean;
  requires_acceptance: boolean;
  penalty_enabled: boolean;
  penalty_rate: string | null;
  is_active: boolean;
}

export interface PartnerDetail extends Partner {
  documents: PartnerDocument[];
  readiness: PartnerReadiness;
  has_signed_contract: boolean;
}

export interface PenaltySummary {
  currency: string;
  total_penalty: string;
  total_outstanding: string;
  max_days_overdue: number;
  lines: { invoice_id: string; number: string; days_overdue: number; outstanding: string; penalty: string }[];
  can_generate: boolean;
  blocked_reason: string | null;
}

export interface IssuedInvoice {
  id: string;
  number: string | null;
  lifecycle: IssuedLifecycle;
  kind: "standard" | "penalty";
  doc_type: "invoice" | "credit_note";
  corrected_invoice_id: string | null;
  credited_total: string;
  issuer_id: string | null;
  partner_id: string | null;
  issue_date: string;
  supply_date: string | null;
  due_date: string | null;
  currency: string;
  buyer_name: string;
  buyer_vat_number: string | null;
  vat_scheme: string;
  note: string | null;
  po_reference: string | null;
  tax_exemption_reason: string | null;
  subtotal: string;
  tax_total: string;
  total: string;
  buyer_email: string | null;
  amount_paid: string;
  paid_date: string | null;
  status: IssuedStatus;
  outstanding: string;
  penalty_rate: string | null;
  penalty_accrued: string;
  days_overdue: number;
  reminder_count: number;
  last_reminder_at: string | null;
  sent_at: string | null;
  viewed_at: string | null;
  voided_at: string | null;
  void_reason: string | null;
  disputed_at: string | null;
  dispute_reason: string | null;
  written_off_at: string | null;
  writeoff_reason: string | null;
  approved_at: string | null;
  issued_at: string | null;
}

// Accounts-receivable cash: a receipt (money received) allocated across invoices.
export interface ReceiptAllocation {
  id: string;
  issued_invoice_id: string;
  amount: string;
  paid_on: string;
  reference: string | null;
  note: string | null;
}

export interface Receipt {
  id: string;
  amount: string;
  received_on: string;
  method: string;
  reference: string | null;
  note: string | null;
  allocated: string;
  unallocated: string;
  created_at: string;
}

export interface ReceiptDetail extends Receipt {
  allocations: ReceiptAllocation[];
}

// Bank reconciliation (Phase 12): imported statements + their lines.
export interface BankStatement {
  id: string;
  filename: string;
  source_format: string;
  method: string;
  line_count: number;
  created_by: string | null;
  created_at: string;
  unmatched: number;
  matched: number;
  ignored: number;
}

export interface BankLine {
  id: string;
  statement_id: string;
  line_date: string;
  description: string;
  amount: string; // signed: credit +, debit −
  direction: "credit" | "debit";
  balance: string | null;
  status: "unmatched" | "matched" | "ignored";
  matched_kind: "receipt" | "reimbursement" | null;
  matched_id: string | null;
}

export interface MatchCandidate {
  kind: "receipt" | "reimbursement";
  id: string;
  amount: string;
  date: string;
  reference: string | null;
  days_off: number;
  score: number;
}

export interface EmailMessage {
  id: string;
  invoice_id: string | null;
  kind: "invoice" | "reminder";
  to_email: string;
  subject: string;
  status: "recorded" | "sent" | "failed";
  error: string | null;
  created_at: string;
}

export interface SendResult {
  message: EmailMessage;
  delivered: boolean;
}

export interface BulkReminderResult {
  sent: number;
  skipped_no_email: number;
  messages: EmailMessage[];
}

export type RecurringFrequency = "weekly" | "monthly" | "quarterly" | "yearly";

export interface RecurringSchedule {
  id: string;
  title: string | null;
  partner_id: string | null;
  frequency: RecurringFrequency;
  interval: number;
  start_date: string;
  next_run_date: string;
  end_date: string | null;
  active: boolean;
  payment_terms_days: number | null;
  last_generated_at: string | null;
  generated_count: number;
  created_at: string;
}

export interface GenerateResult {
  generated: number;
  numbers: string[];
}

export interface WebhookEndpoint {
  id: string;
  url: string;
  events: string;
  description: string | null;
  active: boolean;
  created_at: string;
}

export interface WebhookCreated extends WebhookEndpoint {
  secret: string;
}

export interface WebhookDelivery {
  id: string;
  event_type: string;
  status: "pending" | "delivered" | "failed";
  attempts: number;
  response_code: number | null;
  last_error: string | null;
  created_at: string;
  delivered_at: string | null;
}

export interface IntegrityReport {
  checked: number;
  ok: number;
  healthy: boolean;
  issues: { kind: string; entity_id: string; problem: string; detail: string }[];
}

// --- Issuing reports ---
export interface IssuedSummaryReport {
  currency: string;
  available_currencies: string[];
  count: number;
  net: string;
  vat: string;
  gross: string;
  collected: string;
  outstanding: string;
  series: { period: string; net: string; gross: string; count: number }[];
}

export interface IssuedReceivablesReport {
  currency: string;
  available_currencies: string[];
  statuses: { status: IssuedStatus; label: string; count: number; gross: string; outstanding: string }[];
  aging: { label: string; count: number; outstanding: string }[];
  total_outstanding: string;
  overdue_outstanding: string;
  penalty_accrued: string;
  avg_days_to_pay: number | null;
}

export interface IssuedPartnerReport {
  currency: string;
  available_currencies: string[];
  partners: {
    partner: string; vat_number: string | null; count: number;
    net: string; vat: string; gross: string; outstanding: string; last_invoice: string | null;
  }[];
}

export interface IssuedVatReport {
  currency: string;
  available_currencies: string[];
  by_rate: { rate: string; base: string; vat: string }[];
  by_scheme: { scheme: string; net: string; vat: string }[];
  total_net: string;
  total_vat: string;
}

export interface IssuedInvoiceDetail extends IssuedInvoice {
  lines: {
    position: number;
    description: string;
    quantity: string;
    unit: string;
    unit_price: string;
    discount_percent: string;
    vat_rate: string;
    net_amount: string;
  }[];
  vat_breakdown: VatBucket[];
}
