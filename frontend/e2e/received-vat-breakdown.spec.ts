import { test, expect, type Page } from "@playwright/test";

async function setup(page: Page, mode: "mixed" | "missing" | "mismatch", review = false) {
  const organization = { id: "org-vat", name: "Synthetic VAT", status: "active" };
  const user = { id: "user-vat", org_id: organization.id, name: "Owner", role: "owner", email: "owner@example.com" };
  const vat = { complete: mode !== "missing", matches_header: mode === "missing" ? null : mode !== "mismatch", rates: [
    { tax_rate: "0.00", net: "50.00", tax: "0.00", gross: "50.00", line_count: 1 },
    { tax_rate: "21.00", net: "100.00", tax: mode === "missing" ? null : "21.00", gross: "121.00", line_count: 2 },
  ] };
  const invoice = { id: "vat-1", vendor_id: "vendor-1", vendor_name: "Synthetic Supplier", invoice_number: "VAT-1", issue_date: "2026-10-15", due_date: null, currency: "USD", status: "pending", subtotal: "150.00", tax_amount: "21.00", total: "171.00", total_eur: "150.00", validation_status: "none", validation_findings: [], source_filename: null, notes: null, line_items: [], workflow_state: "approved", amount_paid: "0.00", outstanding: "171.00", payment_status: "open", vat_breakdown: vat };
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "synthetic-token"));
  await page.route("**/api/v1/**", async route => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    let data: unknown = [];
    if (path === "/auth/me") data = { user, organization };
    if (path === "/auth/organizations") data = [{ org_id: organization.id, name: organization.name, current: true }];
    if (path === "/invoices/vat-1/approvals") data = { steps: [], events: [] };
    if (path === "/invoices/vat-1") data = invoice;
    if (path === "/invoices/vat-1/review") data = { ...invoice, lines: [], version: 1, editable: false, locked: true, allowed_targets: [], workflow_label: "Approved", reconciliation: { balanced: true, findings: [] }, approval_steps: [], comments: [], attachments: [] };
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) });
  });
  await page.goto(review ? "/invoices/vat-1/review" : "/invoices/vat-1");
  return page.getByRole("region", { name: "VAT breakdown", exact: true });
}

test("mixed VAT rates retain document currency and separate zero tax", async ({ page }) => {
  const panel = await setup(page, "mixed");
  await expect(panel).toBeVisible();
  await expect(panel.getByRole("row")).toHaveCount(3);
  await expect(panel.getByRole("row").filter({ hasText: "21%" })).toContainText("21.00");
  await expect(panel).toContainText("USD");
  await expect(panel).not.toContainText("Incomplete");
  await expect(panel).not.toContainText("€");
});

test("missing saved tax displays unavailable rather than zero", async ({ page }) => {
  const panel = await setup(page, "missing");
  await expect(panel).toContainText("Incomplete VAT breakdown");
  await expect(panel.getByRole("row").filter({ hasText: "21%" })).toContainText("Unavailable");
  await expect(panel).not.toContainText("Line totals differ");
});

test("review shows header mismatch with source recovery guidance", async ({ page }) => {
  const panel = await setup(page, "mismatch", true);
  await expect(panel).toContainText("Line totals differ from invoice header totals");
  await expect(panel).toContainText("Review the source document before approval");
  await expect(panel).toContainText("Unsaved edits are not included");
});
