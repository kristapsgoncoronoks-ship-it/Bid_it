import { expect, test, type Page } from "@playwright/test";

// Presentation-only fixtures: no OCR, real server writes or currency conversion.
const ORG = { id: "currency-org", name: "Synthetic currency review", status: "active" };
const BASE = {
  invoice_number: "CUR-100", vendor_name: "Synthetic Supplier", issue_date: "2026-10-22",
  due_date: null, source_filename: "currency-source.pdf", po_reference: null,
  supplier_registration_number: null, supplier_vat_id: null, supplier_iban: null,
  line_items: [{ description: "Service", quantity: "1", unit_price: "100.00", amount: "100.00", tax_rate: "0", category: "uncategorized", captured_tax_amount: null, captured_gross_amount: null }],
};

async function setup(page: Page, currency: string | null, appliedCurrency = "USD") {
  const state = {
    currency, fingerprint: "b".repeat(64), read: null as Record<string, unknown> | null,
    reviewPosts: [] as Record<string, unknown>[], applyPosts: [] as Record<string, unknown>[],
    snapshotReads: 0, invoicePosts: 0,
  };
  const draft = () => ({ ...BASE, currency: state.currency });
  const fields = () => [{ field: "currency", line_index: null, value: currency, original_value: currency,
    normalized_value: currency, reviewed_value: state.currency === currency ? null : state.currency,
    status: currency ? "extracted" : "missing", low_confidence: false, confidence: null, provider: "csv", hint: null }];
  const output = () => ({ ...state.read, extraction_run_id: "currency-run", review_fingerprint: state.fingerprint,
    current_draft: draft(), reviewed_fields: [], applied: state.read?.status === "applied", error_code: null, error_detail: null });
  const json = (body: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "synthetic-currency-token"));
  await page.route("**/api/v1/**", async route => {
    const req = route.request(), path = new URL(req.url()).pathname.replace("/api/v1", "");
    if (path === "/auth/me") return route.fulfill(json({ user: { id: "currency-user", org_id: ORG.id, role: "owner", name: "Synthetic Owner", email: "owner@example.test" }, organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/modules") return route.fulfill(json([]));
    if (path === "/invoices/upload/currency-run") return route.fulfill(json({ status: "parsed", extraction_run_id: "currency-run", method: "ocr", draft: { draft: draft(), warnings: [], fields: [] } }));
    if (path === "/invoices/captures/currency-run/fields") return route.fulfill(json(fields()));
    if (path === "/invoices/captures/currency-run/review") {
      const body = req.postDataJSON(); state.reviewPosts.push(body);
      if (body.expected_review_fingerprint !== state.fingerprint) return route.fulfill(json({ detail: "Review changed", code: "capture_review_changed" }, 409));
      const correction = body.fields.find((field: { field: string }) => field.field === "currency");
      if (correction) state.currency = correction.reviewed_value;
      state.fingerprint = "c".repeat(64);
      return route.fulfill({ ...json(fields()), headers: { "x-capture-review-fingerprint": state.fingerprint } });
    }
    if (path === "/invoices/captures/currency-run/pages") return route.fulfill(json({ status: "ready", pages: [{ number: 1, width: 400, height: 600, size: 100 }], total_pages: 1, truncated: false }));
    if (path.endsWith("/pages/1/doc")) return route.fulfill({ contentType: "image/svg+xml", body: '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="600"><rect width="400" height="600" fill="white"/><text x="30" y="70">Synthetic invoice</text></svg>' });
    if (path === "/invoices/captures/currency-run/region-reads") {
      if (req.method() === "GET") {
        state.snapshotReads++;
        return route.fulfill(json({ items: state.read ? [output()] : [], source_sha256: "a".repeat(64), attempt_seq: 0, current_draft: draft(), review_fingerprint: state.fingerprint, reviewed_fields: [], eligible: true, reason: null }));
      }
      const request = req.postDataJSON();
      state.read = { id: "currency-read", command_key: request.command_key, status: "ready", request,
        result: { text: appliedCurrency, engine: "Synthetic reader", warnings: [], rows: [], fields: [{ field: "currency", line_index: null, value: appliedCurrency }] } };
      return route.fulfill(json(output(), 202));
    }
    if (path === "/invoices/captures/currency-run/region-reads/currency-read") return route.fulfill(json(output()));
    if (path.endsWith("/region-reads/currency-read/apply")) {
      const body = req.postDataJSON(); state.applyPosts.push(body);
      if (body.expected_review_fingerprint !== state.fingerprint) return route.fulfill(json({ detail: "Review changed", code: "capture_review_changed" }, 409));
      state.currency = appliedCurrency; state.fingerprint = "d".repeat(64);
      state.read = { ...state.read!, status: "applied", apply_command_key: body.command_key,
        applied_result: { draft: draft(), review_fingerprint: state.fingerprint, changed_fields: 1, appended_rows: 0 } };
      return route.fulfill(json(output()));
    }
    if (path === "/invoices" && req.method() === "POST") { state.invoicePosts++; return route.fulfill(json({ detail: "No invoice writes expected" }, 500)); }
    if (path === "/invoices/duplicate-candidates") return route.fulfill(json({ exact: [], cross_supplier: [], scored: [] }));
    if (path.startsWith("/vendors/resolve")) return route.fulfill(json({ outcome: "create", reason: "Synthetic supplier", candidates: [] }));
    return route.fulfill(json([]));
  });
  await page.goto("/captures/currency-run");
  await expect(page.getByLabel("Currency", { exact: true })).toHaveValue(currency ?? "");
  return state;
}
const subtotal = (page: Page) => page.getByText(/^Net of tax · subtotal /);

for (const [currency, expected] of [["EUR", "€100.00"], ["USD", "US$100.00"]]) {
  test(`capture subtotal preserves ${currency} without conversion`, async ({ page }) => {
    const errors: string[] = []; page.on("pageerror", error => errors.push(error.message));
    const state = await setup(page, currency);
    await expect(subtotal(page)).toHaveText(`Net of tax · subtotal ${expected}`);
    expect(state.invoicePosts).toBe(0); expect(errors).toEqual([]);
  });
}
for (const currency of ["", null, "US", "EURO"] as const) {
  test(`capture subtotal explains ${currency === null ? "null" : JSON.stringify(currency)} currency`, async ({ page }) => {
    const errors: string[] = []; page.on("pageerror", error => errors.push(error.message));
    await setup(page, currency);
    await expect(subtotal(page)).toContainText(currency ? "Invalid currency" : "Currency not set");
    await expect(subtotal(page)).not.toContainText("€");
    expect(errors).toEqual([]);
  });
}

test("the reserved unknown currency code stays explicit instead of becoming a generic symbol", async ({ page }) => {
  await setup(page, "XXX");
  await expect(subtotal(page)).toHaveText("Net of tax · subtotal 100 · Unknown currency (XXX)");
});

test("manual currency correction updates preview before and after the saved review receipt", async ({ page }) => {
  const state = await setup(page, "EUR");
  await page.getByLabel("Currency", { exact: true }).fill("USD");
  await expect(subtotal(page)).toHaveText("Net of tax · subtotal US$100.00");
  await page.getByRole("button", { name: "Save corrections", exact: true }).click();
  await expect.poll(() => state.reviewPosts.length).toBe(1);
  expect(state.reviewPosts[0]).toEqual({ expected_review_fingerprint: "b".repeat(64), fields: [{ field: "currency", reviewed_value: "USD" }] });
  await expect(subtotal(page)).toHaveText("Net of tax · subtotal US$100.00");
  expect(state.invoicePosts).toBe(0);
});

test("region currency application updates subtotal from canonical saved-review readback", async ({ page }) => {
  const state = await setup(page, "EUR");
  await page.getByRole("button", { name: "Select area to re-read", exact: true }).click();
  const panel = page.getByRole("region", { name: "Visual OCR adjustment", exact: true });
  await panel.getByRole("combobox", { name: "Read as", exact: true }).selectOption("header:currency");
  await panel.getByRole("button", { name: "Select whole page", exact: true }).click();
  await panel.getByRole("button", { name: "Re-read selected area", exact: true }).click();
  await panel.getByRole("checkbox", { name: "Apply Currency", exact: true }).check();
  const before = state.snapshotReads;
  await panel.getByRole("button", { name: "Apply selected changes", exact: true }).click();
  await expect.poll(() => state.snapshotReads).toBeGreaterThan(before);
  await expect(page.getByLabel("Currency", { exact: true })).toHaveValue("USD");
  await expect(subtotal(page)).toHaveText("Net of tax · subtotal US$100.00");
  expect(state.applyPosts).toHaveLength(1);
  expect(state.applyPosts[0]).toMatchObject({ expected_review_fingerprint: "b".repeat(64), fields: [{ field: "currency", line_index: null, value: "USD" }], rows: [] });
  expect(state.invoicePosts).toBe(0);
});
