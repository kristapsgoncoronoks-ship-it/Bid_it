import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * N1 — the supplier's purchase-order reference (EN-16931 BT-13) on a RECEIVED
 * invoice, over the LIVE app shell with the API mocked via `page.route`.
 *
 * The backend now captures BT-13 from inbound UBL/CII and from CSV/JSON
 * sources. Capturing it is worth nothing if the approver cannot see it: matching
 * a supplier invoice to a purchase order is the first thing they do. What these
 * tests pin down is the half that only exists on the client:
 *
 *  - **A quoted PO reference is shown**, under the same label the issuing side
 *    uses, so one workspace has one name for one field.
 *  - **An invoice with no PO reference shows no empty slot.** A blank "PO
 *    reference" field reads as "no PO required" — the opposite of "the supplier
 *    did not quote one", which is what null actually means.
 *
 * Synthetic fixtures only.
 */

const ORG = { id: "org-1", name: "Test Workspace", status: "active" };
const USER = {
  id: "user-1",
  email: "owner@example.com",
  name: "Test User",
  role: "owner",
  org_id: "org-1",
  is_platform_admin: false,
};

const INVOICE = {
  id: "inv-1",
  vendor_id: "v-1",
  vendor_name: "Northwind Traders GmbH",
  invoice_number: "INV-2026-0041",
  issue_date: "2026-06-01",
  due_date: null,
  currency: "EUR",
  status: "pending",
  subtotal: "1000.00",
  tax_amount: "240.50",
  total: "1240.50",
  total_eur: "1240.50",
  fx_rate: null,
  fx_source: null,
  validation_status: "none",
  validation_findings: [],
  validated_by: null,
  validated_at: null,
  source_filename: null,
  notes: null,
  po_reference: null as string | null,
  cost_center: null,
  department: null,
  project: null,
  vehicle: null,
  property_ref: null,
  line_items: [],
  workflow_state: "approved",
  amount_paid: "0.00",
  paid_date: null,
  outstanding: "1240.50",
  payment_status: "open",
};

async function open(page: Page, poReference: string | null) {
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));

  const json = (body: unknown, code = 200) => ({
    status: code,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

  await page.route("**/api/v1/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^.*\/api\/v1/, "");

    if (path === "/auth/me") return route.fulfill(json({ user: USER, organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/modules") return route.fulfill(json([]));

    if (path === "/invoices/inv-1") {
      return route.fulfill(json({ ...INVOICE, po_reference: poReference }));
    }
    if (path.startsWith("/invoices/inv-1/")) return route.fulfill(json([]));

    return route.fulfill(json({ items: [], total: 0 }));
  });

  await page.goto("/invoices/inv-1");
}

test("a quoted purchase-order reference is shown to the approver", async ({ page }) => {
  await open(page, "PO-2026-0447");

  await expect(page.getByText("INV-2026-0041")).toBeVisible();
  await expect(page.getByText("PO reference")).toBeVisible();
  await expect(page.getByText("PO-2026-0447")).toBeVisible();
});

test("an invoice with no purchase-order reference shows no empty slot", async ({ page }) => {
  await open(page, null);

  await expect(page.getByText("INV-2026-0041")).toBeVisible();
  await expect(page.getByText("PO reference")).toHaveCount(0);
});
