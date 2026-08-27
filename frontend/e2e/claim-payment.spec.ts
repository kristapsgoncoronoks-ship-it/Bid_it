import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * WO-T — the claim lifecycle's last edge, in the browser.
 *
 * `recovery.median_days_to_refund` reported `null` in every workspace for a
 * whole arc, because nothing wrote either end of the interval it measures and
 * no transition existed past `approved`. These specs cover the operator half of
 * the fix, and the design rule that carries it:
 *
 * **The amount received is typed, never prefilled from the approved base.** A
 * member state does not always pay what it approved, and that difference is
 * precisely the fact worth recording — a prefilled field is an invitation to
 * record a match that did not happen. So one spec is an ABSENCE: the amount
 * field starts empty even though the approved figure is on screen beside it.
 */

const ORG = { id: "org-1", name: "Test Workspace", status: "active" };
const USER = {
  id: "user-1",
  email: "owner@test.io",
  name: "Owner",
  role: "owner",
  org_id: "org-1",
};
const TRANSPORT_MODULE = { key: "transport", name: "Transport & VAT refunds", enabled: true };

const APPROVED_CLAIM = {
  id: "claim-1",
  entity_id: "ent-1",
  refund_country: "LV",
  ref_period: "2026-Q2",
  status: "approved",
  status_code: "3",
  status_note: null,
  decision_date: "2026-06-01",
  action_deadline: null,
  submitted_date: "2026-05-01",
  approved_date: "2026-06-01",
  paid_date: null,
  paid_amount: null,
  vat_eur: "360.00",
  vat_local: "360.00",
  currency: "EUR",
  fee_pct: "10.00",
  fee_min: "25.00",
  fee_eur: "36.00",
  created_at: "2026-04-01T09:00:00Z",
};

const PAID_CLAIM = {
  ...APPROVED_CLAIM,
  status: "paid",
  paid_date: "2026-06-30",
  paid_amount: "312.40",
};

async function mockApi(
  page: Page,
  opts: { claim?: unknown; payment?: { status: number; body: unknown } } = {},
): Promise<void> {
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));
  const json = (body: unknown, status = 200) => ({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
  let claim = opts.claim ?? APPROVED_CLAIM;
  await page.route("**/api/v1/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^.*\/api\/v1/, "");
    if (path === "/auth/me") return route.fulfill(json({ user: USER, organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/modules") return route.fulfill(json([TRANSPORT_MODULE]));
    if (path === "/transport/claims/claim-1/payment") {
      if (opts.payment) return route.fulfill(json(opts.payment.body, opts.payment.status));
      claim = PAID_CLAIM;
      return route.fulfill(json(PAID_CLAIM));
    }
    if (path === "/transport/claims/claim-1") return route.fulfill(json(claim));
    if (path.startsWith("/transport/claims/claim-1/")) return route.fulfill(json([]));
    return route.fulfill(json([]));
  });
}

test("an approved claim offers to record the refund, and shows what landed", async ({ page }) => {
  await mockApi(page);
  await page.goto("/vat-claims/claim-1");

  const open = page.getByRole("button", { name: "Record refund received" });
  await expect(open).toBeVisible();
  await open.click();

  await page.getByLabel("Amount received").fill("312.40");
  await page.getByLabel("Date received").fill("2026-06-30");
  await page.getByRole("button", { name: "Record", exact: true }).click();

  // The "Refund received" figure on the totals card has existed since WO-78
  // and rendered an em dash forever, because nothing wrote `paid_amount`.
  // Asserting the VALUE is what proves WO-T filled it — and it is unambiguous,
  // unlike the label, which the page carries in two places.
  await expect(page.getByText("€312.40")).toBeVisible();
});

test("the amount is never prefilled from the approved base", async ({ page }) => {
  await mockApi(page);
  await page.goto("/vat-claims/claim-1");

  await page.getByRole("button", { name: "Record refund received" }).click();

  // Positive anchor: the approved base IS shown, so the operator can compare.
  await expect(page.getByText("Approved base: 360.00 EUR")).toBeVisible();
  // The absence that matters: knowing the approved figure did not put it in
  // the field. A prefilled amount would make "they paid in full" the default
  // answer to a question nobody asked.
  await expect(page.getByLabel("Amount received")).toHaveValue("");
});

test("a claim that is not approved offers no refund control", async ({ page }) => {
  await mockApi(page, { claim: { ...APPROVED_CLAIM, status: "submitted", approved_date: null } });
  await page.goto("/vat-claims/claim-1");

  // Anchor first: the page rendered this claim.
  await expect(page.getByRole("heading", { name: "LV · 2026-Q2" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Record refund received" })).toHaveCount(0);
});

test("a refused payment renders the server's sentence", async ({ page }) => {
  await mockApi(page, {
    payment: {
      status: 409,
      body: { detail: "This claim is already recorded as paid", code: "claim_already_paid" },
    },
  });
  await page.goto("/vat-claims/claim-1");

  await page.getByRole("button", { name: "Record refund received" }).click();
  await page.getByLabel("Amount received").fill("312.40");
  await page.getByRole("button", { name: "Record", exact: true }).click();

  await expect(page.getByText("This claim is already recorded as paid")).toBeVisible();
});
