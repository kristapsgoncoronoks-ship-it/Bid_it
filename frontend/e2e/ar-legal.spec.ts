import { test, expect, type Page } from "@playwright/test";

/**
 * WO-K AR legal trio over the LIVE app shell, API mocked via `page.route`.
 *
 * What earns its place here:
 *  - a credit-note row states which invoice it corrects (Art. 219 made
 *    visible in the register, not only on the PDF);
 *  - an overdue row's "statutory interest?" peek fetches the detail once and
 *    renders the advisory 2011/7/EU figure;
 *  - the dunning screen's reference-rate card saves the configured rate;
 *  - copy is industry-neutral (owner guard list).
 *
 * Synthetic fixtures only.
 */

const ORG = { id: "org-1", name: "Test Workspace", status: "active" };
const USER = { id: "user-1", email: "owner@test.io", name: "Owner", role: "owner", org_id: "org-1" };

const ISSUER = {
  id: "issuer-1",
  name: "Fictional Freight OÜ",
  is_default: true,
  legal_name: "Fictional Freight OÜ",
  trade_name: null,
  vat_number: null,
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

function invoice(id: string, number: string, extra: Record<string, unknown> = {}) {
  return {
    id,
    number,
    lifecycle: "issued",
    kind: "standard",
    doc_type: "invoice",
    corrected_invoice_id: null,
    corrected_invoice_number: null,
    credited_total: "0.00",
    issuer_id: ISSUER.id,
    partner_id: null,
    issue_date: "2026-06-01",
    supply_date: null,
    due_date: "2026-06-15",
    currency: "EUR",
    buyer_name: "Fictional Buyer OÜ",
    buyer_vat_number: null,
    vat_scheme: "standard",
    note: null,
    po_reference: null,
    tax_exemption_reason: null,
    subtotal: "500.00",
    tax_total: "0.00",
    total: "500.00",
    buyer_email: null,
    amount_paid: "0.00",
    paid_date: null,
    status: "issued",
    outstanding: "500.00",
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
    ...extra,
  };
}

const OVERDUE = invoice("inv-over", "INV-2026-0044", { status: "overdue", days_overdue: 73 });
const CREDIT = invoice("cn-1", "CN-2026-0007", {
  doc_type: "credit_note",
  corrected_invoice_id: "inv-orig",
  corrected_invoice_number: "INV-2026-0040",
});

async function mockApi(page: Page): Promise<{ detailFetches: number; ratePuts: unknown[] }> {
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));
  const state = { detailFetches: 0, ratePuts: [] as unknown[] };

  const json = (body: unknown, status = 200) => ({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

  await page.route("**/api/v1/**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace(/^.*\/api\/v1/, "");
    const method = req.method();

    if (path === "/auth/me") return route.fulfill(json({ user: USER, organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/modules") return route.fulfill(json(MODULES));
    if (path === "/issuer" && method === "GET") return route.fulfill(json(ISSUER));
    if (path === "/issuer/registry") return route.fulfill(json([ISSUER]));
    if (path === "/partners") return route.fulfill(json([]));
    if (path === "/issued/recurring") return route.fulfill(json([]));
    if (path === "/issued" && method === "GET")
      return route.fulfill(json({ items: [OVERDUE, CREDIT], total: 2 }));
    if (path === `/issued/${OVERDUE.id}` && method === "GET") {
      state.detailFetches += 1;
      return route.fulfill(
        json({
          ...OVERDUE,
          lines: [],
          vat_breakdown: [],
          buyer_address_line1: null,
          buyer_city: null,
          buyer_postal_code: null,
          buyer_country: null,
          late_interest: {
            basis: "statutory",
            directive: "2011/7/EU",
            base_rate_pp: "2.15",
            base_rate_configured: false,
            rate_pp: "10.15",
            days_overdue: 73,
            outstanding: "500.00",
            interest_eur: "10.15",
            recovery_cost_eur: "40.00",
            total_eur: "50.15",
          },
        }),
      );
    }
    if (path === "/dunning/policy")
      return route.fulfill(json({ is_default: true, levels: [] }));
    if (path === "/settings/late-interest") {
      if (method === "PUT") {
        state.ratePuts.push(req.postDataJSON());
        return route.fulfill(
          json({ base_rate_pp: "4.00", default_base_rate_pp: "2.15", statutory_margin_pp: "8" }),
        );
      }
      return route.fulfill(
        json({ base_rate_pp: null, default_base_rate_pp: "2.15", statutory_margin_pp: "8" }),
      );
    }
    return route.fulfill(json({ items: [], total: 0 }));
  });

  return state;
}

test("a credit-note row names the invoice it corrects", async ({ page }) => {
  await mockApi(page);
  await page.goto("/issue");
  await expect(page.getByText("CN-2026-0007")).toBeVisible();
  await expect(page.getByText("corrects INV-2026-0040")).toBeVisible();
});

test("the statutory-interest peek fetches once and shows the advisory figure", async ({ page }) => {
  const state = await mockApi(page);
  await page.goto("/issue");
  await expect(page.getByText("73d overdue")).toBeVisible();

  await page.getByRole("button", { name: "statutory interest?" }).click();
  await expect(page.getByText("statutory 50.15 €")).toBeVisible();
  expect(state.detailFetches).toBe(1);
});

test("the dunning screen saves the reference rate", async ({ page }) => {
  const state = await mockApi(page);
  await page.goto("/dunning");
  await expect(
    page.getByRole("heading", { name: "Statutory late-payment interest" }),
  ).toBeVisible();
  await expect(page.getByText("Directive 2011/7/EU", { exact: false })).toBeVisible();

  await page.getByLabel("Reference rate percent").fill("4.00");
  await page.getByRole("button", { name: "Save rate" }).click();
  await expect.poll(() => state.ratePuts.length).toBe(1);
  expect(state.ratePuts[0]).toEqual({ base_rate_pp: "4.00" });
});

test("the ar-legal copy is industry-neutral", async ({ page }) => {
  await mockApi(page);
  await page.goto("/dunning");
  await expect(
    page.getByRole("heading", { name: "Statutory late-payment interest" }),
  ).toBeVisible();
  const text = (await page.locator("body").innerText()).toLowerCase();
  for (const word of ["cargo", "fuel", "vehicle", "driver", "truck", "site crew"]) {
    expect(text).not.toContain(word);
  }
});
