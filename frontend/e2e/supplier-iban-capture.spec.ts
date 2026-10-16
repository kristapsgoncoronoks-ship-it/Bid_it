import { test, expect, type Page } from "@playwright/test";

// Public registry examples; synthetic test data only.
const ONE = "DE89370400440532013000";
const TWO = "GB29NWBK60161331926819";
const ORG = { id: "org-bank", name: "Synthetic Bank", status: "active" };
const USER = { id: "user-bank", org_id: ORG.id, name: "Owner", role: "owner", email: "owner@example.com" };

async function setup(page: Page, view: "capture" | "detail" | "review") {
  const header = { invoice_number: "BANK-1", vendor_name: "Synthetic Supplier", issue_date: "2026-10-16", currency: "EUR", supplier_iban: ONE };
  const line = { id: "line-bank", description: "Synthetic service", category: "uncategorized", quantity: "1", unit_price: "100", amount: "100", tax_rate: "0" };
  let savedIban: string | null = ONE;
  let version = 1;
  const fields = Object.entries(header).map(([field, value]) => ({ field, value, original_value: value, normalized_value: value, reviewed_value: null as string | null, status: "extracted", line_index: null }));
  const posts: Record<string, unknown>[] = [];
  const invoice = () => ({ id: "invoice-bank", vendor_id: "vendor-bank", vendor_name: header.vendor_name, invoice_number: header.invoice_number, issue_date: header.issue_date, due_date: null, currency: "EUR", status: "pending", subtotal: "100.00", tax_amount: "0.00", total: "100.00", validation_status: "none", validation_findings: [], source_filename: null, notes: null, line_items: [line], supplier_iban_masked: savedIban ? `…${savedIban.slice(-4)} (len ${savedIban.length})` : null, workflow_state: "draft", amount_paid: "0.00", outstanding: "100.00", payment_status: "open" });
  const review = () => ({ ...invoice(), supplier_iban: savedIban, lines: [line], editable: true, locked: false, version, allowed_targets: [], workflow_label: "Draft", reconciliation: { balanced: true, findings: [] }, approval_steps: [], comments: [], attachments: [] });
  const json = (data: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(data) });
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "synthetic-token"));
  await page.route("**/api/v1/**", async route => {
    const req = route.request(); const path = new URL(req.url()).pathname.replace("/api/v1", "");
    if (path === "/auth/me") return route.fulfill(json({ user: USER, organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/invoices/invoice-bank") return route.fulfill(json(invoice()));
    if (path === "/invoices/invoice-bank/approvals") return route.fulfill(json({ steps: [], events: [] }));
    if (path === "/invoices/invoice-bank/review") {
      if (req.method() === "PATCH") { savedIban = req.postDataJSON().supplier_iban; version++; }
      return route.fulfill(json(review()));
    }
    if (path === "/invoices/upload/run-bank") return route.fulfill(json({ extraction_run_id: "run-bank", status: "parsed", method: "json", draft: { draft: { ...header, line_items: [line], source_filename: "source.json" }, warnings: [], fields } }));
    if (path === "/invoices/captures/run-bank/fields") return route.fulfill(json(fields));
    if (path === "/invoices/captures/run-bank/source") return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
    if (path === "/invoices/captures/run-bank/review") {
      for (const correction of req.postDataJSON().fields) {
        if (correction.field === "supplier_iban" && correction.reviewed_value === "invalid-account") return route.fulfill(json({ detail: "Invalid IBAN: failed the ISO 13616 structure / MOD-97 check-digit test." }, 422));
        const field = fields.find(item => item.field === correction.field);
        if (field) field.reviewed_value = correction.reviewed_value;
      }
      return route.fulfill(json(fields));
    }
    if (path === "/invoices" && req.method() === "POST") { posts.push(req.postDataJSON()); savedIban = req.postDataJSON().supplier_iban; return route.fulfill(json(invoice(), 201)); }
    if (path === "/invoices/duplicate-candidates") return route.fulfill(json({ exact: [], cross_supplier: [], scored: [] }));
    if (path.startsWith("/vendors/resolve")) return route.fulfill(json({ outcome: "create", needs_decision: false, reason: "New synthetic supplier", candidates: [] }));
    return route.fulfill(json([]));
  });
  await page.goto(view === "capture" ? "/captures/run-bank" : `/invoices/invoice-bank${view === "review" ? "/review" : ""}`);
  return { posts, fields };
}

test("captured correction persists through reload and reaches confirmation", async ({ page }) => {
  const state = await setup(page, "capture");
  const input = page.getByLabel("Captured supplier IBAN", { exact: true });
  await expect(input).toHaveValue(ONE);
  await expect(page.getByText(/Captured supplier IBAN is document evidence/)).toBeVisible();
  await input.fill(TWO);
  await page.getByRole("button", { name: "Save corrections", exact: true }).click();
  await expect(page.getByText(/Corrections recorded/)).toBeVisible();
  await page.reload(); await expect(input).toHaveValue(TWO);
  await page.getByRole("button", { name: "Confirm & create invoice", exact: true }).click();
  await expect(page).toHaveURL(/\/invoices\/invoice-bank$/);
  expect(state.posts).toHaveLength(1); expect(state.posts[0].supplier_iban).toBe(TWO);
  expect(state.fields.find(field => field.field === "supplier_iban")?.original_value).toBe(ONE);
  await expect(page.getByText("Captured supplier IBAN: …6819 (len 22)", { exact: true })).toBeVisible();
});

test("invalid capture correction shows recovery and can be cleared", async ({ page }) => {
  const state = await setup(page, "capture");
  const input = page.getByLabel("Captured supplier IBAN", { exact: true });
  await input.fill("invalid-account");
  await page.getByRole("button", { name: "Save corrections", exact: true }).click();
  await expect(page.getByText(/Invalid IBAN/)).toBeVisible();
  await expect(input).toHaveValue("invalid-account");
  await input.fill(""); await page.getByRole("button", { name: "Save corrections", exact: true }).click();
  await expect(page.getByText(/Corrections recorded/)).toBeVisible();
  await page.reload(); await expect(input).toHaveValue("");
  await page.getByRole("button", { name: "Confirm & create invoice", exact: true }).click();
  await expect(page).toHaveURL(/\/invoices\/invoice-bank$/);
  expect(state.posts[0].supplier_iban).toBeNull();
});

test("saved detail masks the account and explains payment authority", async ({ page }) => {
  await setup(page, "detail");
  await expect(page.getByText("Captured supplier IBAN: …3000 (len 22)", { exact: true })).toBeVisible();
  await expect(page.getByText(/Payments use approved supplier bank details/)).toBeVisible();
  await expect(page.locator("body")).not.toContainText(ONE);
});

test("review header correction and clearing persist", async ({ page }) => {
  await setup(page, "review");
  const input = page.getByLabel("Captured supplier IBAN", { exact: true });
  await expect(input).toHaveValue(ONE);
  await input.fill(TWO); await page.getByRole("button", { name: "Save header", exact: true }).click();
  await expect(page.getByText("v2", { exact: true })).toBeVisible();
  await page.reload(); await expect(input).toHaveValue(TWO);
  await input.fill(""); await page.getByRole("button", { name: "Save header", exact: true }).click();
  await expect(page.getByText("v3", { exact: true })).toBeVisible();
  await page.reload(); await expect(input).toHaveValue("");
});
