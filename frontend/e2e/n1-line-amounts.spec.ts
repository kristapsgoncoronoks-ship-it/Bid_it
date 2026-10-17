import { test, expect, type Page } from "@playwright/test";

const ORG = { id: "org-n1", name: "Synthetic N1", status: "active" };
const USER = { id: "user-n1", org_id: ORG.id, name: "Owner", role: "owner", email: "owner@example.com" };
const LINE = { id: "line-1", description: "Synthetic service", category: "uncategorized", quantity: "1", unit_price: "100", amount: "100.00", tax_rate: "21", tax_amount: "21.00", gross_amount: "121.00", captured_tax_amount: "25.00" as string | null, captured_gross_amount: "125.00" as string | null, source_amounts_match: false as boolean | null };
const INVOICE = { id: "invoice-1", vendor_id: "vendor-1", vendor_name: "Synthetic Supplier", invoice_number: "N1-LINE-1", issue_date: "2026-10-13", due_date: null, currency: "EUR", status: "pending", subtotal: "100.00", tax_amount: "21.00", total: "121.00", total_eur: "121.00", validation_status: "none", validation_findings: [], source_filename: null, notes: null, line_items: [LINE], workflow_state: "draft", amount_paid: "0.00", outstanding: "121.00", payment_status: "open" };

async function setup(page: Page, mode: "detail" | "historical" | "capture") {
  let invoice = mode === "historical" ? { ...INVOICE, line_items: [{ ...LINE, tax_amount: null, gross_amount: null, captured_tax_amount: null, captured_gross_amount: null, source_amounts_match: null }] } : { ...INVOICE };
  const header = { invoice_number: "N1-LINE-1", vendor_name: "Synthetic Supplier", issue_date: "2026-10-13", currency: "EUR" };
  const fields = [
    ...Object.entries(header).map(([field, value]) => ({ field, value, original_value: value, normalized_value: value, reviewed_value: null as string | null, status: "extracted", line_index: null })),
    ...Object.entries(LINE).filter(([field]) => ["description", "category", "quantity", "unit_price", "amount", "tax_rate", "captured_tax_amount", "captured_gross_amount"].includes(field)).map(([field, value]) => ({ field, value: String(value), original_value: String(value), normalized_value: String(value), reviewed_value: null as string | null, status: "extracted", line_index: 0 })),
  ];
  const posts: Record<string, unknown>[] = [];
  const json = (data: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(data) });
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "synthetic-token"));
  await page.route("**/api/v1/**", async (route) => {
    const req = route.request(); const path = new URL(req.url()).pathname.replace("/api/v1", "");
    if (path === "/auth/me") return route.fulfill(json({ user: USER, organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/modules") return route.fulfill(json([]));
    if (path === "/invoices/invoice-1") return route.fulfill(json(invoice));
    if (path === "/invoices/upload/run-1") return route.fulfill(json({ extraction_run_id: "run-1", status: "parsed", method: "json", draft: { draft: { ...header, line_items: [{ ...LINE }], source_filename: "source.json" }, warnings: [], fields } }));
    if (path === "/invoices/captures/run-1/fields") return route.fulfill(json(fields));
    if (path === "/invoices/captures/run-1/source") return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
    if (path === "/invoices/captures/run-1/review") {
      for (const correction of req.postDataJSON().fields) {
        const field = fields.find((item) => item.field === correction.field && item.line_index === correction.line_index);
        if (field) field.reviewed_value = correction.reviewed_value;
      }
      return route.fulfill(json({ updated: 2 }));
    }
    if (path === "/invoices" && req.method() === "POST") {
      posts.push(req.postDataJSON());
      invoice = { ...INVOICE, line_items: [{ ...LINE, captured_tax_amount: "21.00", captured_gross_amount: null, source_amounts_match: true }] };
      return route.fulfill(json(invoice, 201));
    }
    if (path === "/invoices/duplicate-candidates") return route.fulfill(json({ exact: [], cross_supplier: [], scored: [] }));
    if (path.startsWith("/vendors/resolve")) return route.fulfill(json({ outcome: "create", needs_decision: false, reason: "New synthetic supplier", candidates: [] }));
    return route.fulfill(json([]));
  });
  await page.goto(mode === "capture" ? "/captures/run-1" : "/invoices/invoice-1");
  return { posts, fields };
}

test("saved detail distinguishes captured tax/gross from calculated amounts", async ({ page }) => {
  await setup(page, "detail");
  await expect(page.getByText(/Calculated tax:.*21/)).toBeVisible();
  await expect(page.getByText(/Captured tax:.*25/)).toBeVisible();
  await expect(page.getByText("Captured amounts differ from calculation; review the source.")).toBeVisible();
});

test("historical lines show unavailable calculation and missing evidence", async ({ page }) => {
  await setup(page, "historical");
  await expect(page.getByText(/Calculated tax: unavailable for historical line/)).toBeVisible();
  await expect(page.getByText(/Captured tax: not provided/)).toBeVisible();
  await expect(page.getByText("Captured amounts differ from calculation; review the source.")).toHaveCount(0);
});

test("capture correction and clearing survive refresh and reach confirmation", async ({ page }) => {
  const state = await setup(page, "capture");
  await page.getByLabel("Line 1 Captured tax", { exact: true }).fill("21");
  await page.getByLabel("Line 1 Captured gross", { exact: true }).fill("");
  await page.getByRole("button", { name: "Save corrections", exact: true }).click();
  await expect(page.getByText(/Corrections recorded/)).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("Line 1 Captured tax", { exact: true })).toHaveValue("21");
  await expect(page.getByLabel("Line 1 Captured gross", { exact: true })).toHaveValue("");
  await page.getByRole("button", { name: "Confirm & create invoice", exact: true }).click();
  await expect(page).toHaveURL(/\/invoices\/invoice-1$/);
  expect(state.posts).toHaveLength(1);
  const lines = state.posts[0].line_items as { captured_tax_amount: string; captured_gross_amount: null }[];
  expect(lines[0].captured_tax_amount).toBe("21"); expect(lines[0].captured_gross_amount).toBeNull();
  const original = state.fields.find((field) => field.field === "captured_tax_amount");
  expect(original?.original_value).toBe("25.00");
});
