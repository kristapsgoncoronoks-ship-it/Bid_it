import { test, expect, type Page } from "@playwright/test";

/**
 * AR "Issue" screen: Void / Write off require an explicit ConfirmDialog before
 * firing (WO-34 / R16), over the LIVE app shell with `page.route` mocking —
 * same deterministic, no-backend pattern as `upload-duplicate.spec.ts`.
 *
 * Prior behaviour used a bare `window.prompt(...)` in the button's `onClick`,
 * which fired the mutation the moment the native prompt was dismissed with OK
 * (even with an empty reason). This spec proves the click now opens a styled
 * confirm dialog first, that cancelling issues NO request, and that confirming
 * calls the expected endpoint exactly once.
 *
 * Synthetic fixtures only (master context §10): fictional names/ids/figures.
 */

const ORG = { id: "org-1", name: "Test Workspace", status: "active" };
const USER = {
  id: "user-1",
  email: "owner@test.io",
  name: "Owner",
  role: "owner",
  org_id: "org-1",
};

const ISSUER = {
  id: "issuer-1",
  name: "Fictional Freight OÜ",
  is_default: true,
  legal_name: "Fictional Freight OÜ",
  trade_name: null,
  vat_number: "EE123456789",
  registration_number: "12345678",
  address_line1: "Testivälja 1",
  address_line2: null,
  city: "Tallinn",
  postal_code: "10111",
  country: "EE",
  email: "billing@fictional-freight.test",
  phone: null,
  iban: null,
  bic: null,
  default_currency: "EUR",
  invoice_prefix: "INV",
  credit_note_prefix: "CN",
  next_number: 42,
  payment_terms_days: 14,
  default_penalty_rate: null,
  payment_instructions: null,
  notes: null,
  is_complete: true,
  missing_fields: [],
  has_logo: false,
};

const MODULES = [
  {
    key: "issuing",
    name: "Invoice issuing",
    description: "Issue customer invoices",
    core: false,
    enabled: true,
    requires_issuer: true,
    ready: true,
  },
];

function invoice(id: string, number: string): Record<string, unknown> {
  return {
    id,
    number,
    lifecycle: "issued",
    kind: "standard",
    doc_type: "invoice",
    corrected_invoice_id: null,
    credited_total: "0.00",
    issuer_id: ISSUER.id,
    partner_id: null,
    issue_date: "2026-07-01",
    supply_date: null,
    due_date: "2026-07-15",
    currency: "EUR",
    buyer_name: "Fictional Buyer OÜ",
    buyer_vat_number: null,
    vat_scheme: "standard",
    note: null,
    po_reference: null,
    tax_exemption_reason: null,
    subtotal: "100.00",
    tax_total: "21.00",
    total: "121.00",
    buyer_email: null,
    amount_paid: "0.00",
    paid_date: null,
    status: "issued",
    outstanding: "121.00",
    penalty_rate: null,
    penalty_accrued: "0.00",
    days_overdue: 0,
    reminder_count: 0,
    last_reminder_at: null,
    sent_at: null,
    viewed_at: null,
    voided_at: null,
    void_reason: null,
    disputed_at: null,
    dispute_reason: null,
    written_off_at: null,
    writeoff_reason: null,
  };
}

const VOID_INVOICE = invoice("inv-void-1", "INV-2026-0042");
const WRITEOFF_INVOICE = invoice("inv-wo-1", "INV-2026-0043");

async function mockApi(page: Page): Promise<{ calls: string[] }> {
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));
  const calls: string[] = [];

  const json = (body: unknown, status = 200) => ({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

  await page.route("**/api/v1/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname.replace(/^.*\/api\/v1/, "");
    const method = req.method();

    if (path === "/auth/me") return route.fulfill(json({ user: USER, organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/modules") return route.fulfill(json(MODULES));
    if (path === "/issuer" && method === "GET") return route.fulfill(json(ISSUER));
    if (path === "/issuer/registry") return route.fulfill(json([ISSUER]));
    if (path === "/partners") return route.fulfill(json([]));
    if (path === "/issued/recurring") return route.fulfill(json([]));
    if (path === "/issued" && method === "GET") {
      return route.fulfill(json({ items: [VOID_INVOICE, WRITEOFF_INVOICE], total: 2 }));
    }
    if (path === `/issued/${VOID_INVOICE.id}/void` && method === "POST") {
      calls.push("void");
      return route.fulfill(json({ ...VOID_INVOICE, voided_at: "2026-07-27T12:00:00Z" }));
    }
    if (path === `/issued/${WRITEOFF_INVOICE.id}/write-off` && method === "POST") {
      calls.push("write-off");
      return route.fulfill(
        json({ ...WRITEOFF_INVOICE, lifecycle: "written_off", written_off_at: "2026-07-27T12:00:00Z" }),
      );
    }

    return route.fulfill(json([]));
  });

  return { calls };
}

test("voiding an invoice requires an explicit confirm", async ({ page }) => {
  const { calls } = await mockApi(page);
  await page.goto("/issue");

  await expect(page.getByRole("heading", { level: 1, name: "Issue invoices" })).toBeVisible();
  const row = page.locator("tr", { hasText: VOID_INVOICE.number as string });

  // Clicking "Void" opens the confirm dialog — it must NOT fire the request yet.
  await row.getByRole("button", { name: "Void" }).click();
  const dialog = page.getByRole("dialog", { name: /Void invoice/ });
  await expect(dialog).toBeVisible();
  expect(calls).not.toContain("void");

  // Cancelling leaves the invoice unvoided and issues no request.
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
  expect(calls).not.toContain("void");

  // Re-open and confirm — exactly one call to /void, dialog closes.
  await row.getByRole("button", { name: "Void" }).click();
  await expect(page.getByRole("dialog", { name: /Void invoice/ })).toBeVisible();
  await page.getByRole("dialog", { name: /Void invoice/ }).getByRole("button", { name: "Void invoice" }).click();
  await expect(page.getByRole("dialog", { name: /Void invoice/ })).toBeHidden();
  expect(calls.filter((c) => c === "void")).toHaveLength(1);
});

test("writing off an invoice requires an explicit confirm", async ({ page }) => {
  const { calls } = await mockApi(page);
  await page.goto("/issue");

  await expect(page.getByRole("heading", { level: 1, name: "Issue invoices" })).toBeVisible();
  const row = page.locator("tr", { hasText: WRITEOFF_INVOICE.number as string });

  // Clicking "Write off" opens the confirm dialog — it must NOT fire the request yet.
  await row.getByRole("button", { name: "Write off" }).click();
  const dialog = page.getByRole("dialog", { name: /Write off invoice/ });
  await expect(dialog).toBeVisible();
  expect(calls).not.toContain("write-off");

  // Cancelling leaves the invoice as-is and issues no request.
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
  expect(calls).not.toContain("write-off");

  // Re-open and confirm — exactly one call to /write-off, dialog closes.
  await row.getByRole("button", { name: "Write off" }).click();
  await expect(page.getByRole("dialog", { name: /Write off invoice/ })).toBeVisible();
  await page
    .getByRole("dialog", { name: /Write off invoice/ })
    .getByRole("button", { name: "Write off as bad debt" })
    .click();
  await expect(page.getByRole("dialog", { name: /Write off invoice/ })).toBeHidden();
  expect(calls.filter((c) => c === "write-off")).toHaveLength(1);
});
