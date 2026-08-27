import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * WO-U — three shipped surfaces nobody could reach.
 *
 * Each of these existed and worked; none of them could be found. That is a
 * distinct class of defect from a bug, and it is invisible to every test that
 * navigates by URL — which is why these specs navigate the way a person does,
 * through the menu, and why the fee-rate spec asserts a round trip rather than
 * a render.
 *
 * (b), the raw entity id the excise report showed beside every entity name,
 * is asserted in `excise.spec.ts` instead — that suite already owns a correct
 * excise fixture, and the line it used to carry pinned the uuid rather than the
 * name, so the absence belongs exactly where the claim used to be.
 *
 * The fee-rate one carries real stakes: `lock.submit_claim` refuses
 * `fee_rate_not_configured` until a rung resolves, so for as long as this screen
 * did not exist, an org that had bought the product could not file a single
 * claim through it. The only way to open the gate was a Python shell.
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
const ISSUING_MODULE = { key: "issuing", name: "Customer invoicing", enabled: true };
const EXPENSES_MODULE = { key: "expenses", name: "Expenses", enabled: true };
const ENTITY = { id: "entity-1", name: "Demo Haulage OU", legal_name: "Demo Haulage OU" };

const STANDARD_RATE = {
  id: "rate-1",
  entity_id: null,
  country: "",
  fee_pct: "15.00",
  fee_min: "50.00",
  kind: "standard",
  standard_pct: "15.00",
  standard_min: "50.00",
  pct_discount: null,
  min_discount: null,
};

async function mockApi(page: Page, opts: { rates?: unknown[] } = {}): Promise<void> {
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));
  const json = (body: unknown, status = 200) => ({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
  let rates = opts.rates ?? [];
  await page.route("**/api/v1/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^.*\/api\/v1/, "");
    const method = route.request().method();
    if (path === "/auth/me") return route.fulfill(json({ user: USER, organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/modules")
      return route.fulfill(json([TRANSPORT_MODULE, ISSUING_MODULE, EXPENSES_MODULE]));
    if (path === "/issuer/registry") return route.fulfill(json([ENTITY]));
    if (path === "/transport/fee-rates") {
      if (method === "PUT") {
        rates = [STANDARD_RATE];
        return route.fulfill(json(STANDARD_RATE));
      }
      return route.fulfill(json(rates));
    }
    return route.fulfill(json([]));
  });
}

// --------------------------------------------------------------------------- #
// (a) the fee-rate screen
// --------------------------------------------------------------------------- #

test("the fee-rate screen says plainly that no claim can be filed without one", async ({
  page,
}) => {
  await mockApi(page);
  await page.goto("/vat-admin");

  await page.getByRole("tab", { name: "Fee rates" }).click();

  await expect(page.getByText("No fee rate is configured")).toBeVisible();
  // The consequence, in the copy — this emptiness is not cosmetic.
  await expect(
    page.getByText("no claim can be filed", { exact: false }),
  ).toBeVisible();
});

test("a fee rate can be set and comes back on the list", async ({ page }) => {
  await mockApi(page);
  await page.goto("/vat-admin");
  await page.getByRole("tab", { name: "Fee rates" }).click();

  await page.getByLabel("Percentage").fill("15.00");
  await page.getByLabel("Minimum (EUR)").fill("50.00");
  await page.getByRole("button", { name: "Set this rate" }).click();

  await expect(page.getByText("Every customer (org standard)")).toBeVisible();
  await expect(page.getByText("15.00%")).toBeVisible();
  await expect(page.getByText("This is the standard")).toBeVisible();
});

test("a discount is offered as a way to arrive at a price, not as a stored one", async ({
  page,
}) => {
  await mockApi(page, { rates: [STANDARD_RATE] });
  await page.goto("/vat-admin");
  await page.getByRole("tab", { name: "Fee rates" }).click();

  // Positive: the negotiate card exists and the customer picker resolved.
  await expect(page.getByText("Negotiate a customer off the standard")).toBeVisible();
  await expect(page.getByLabel("Customer").locator("option")).toHaveCount(2);
  // The rule that makes it safe, stated where the operator reads it.
  await expect(
    page.getByText("not what is stored", { exact: false }),
  ).toBeVisible();
});

// --------------------------------------------------------------------------- #
// (c) the nav entries
// --------------------------------------------------------------------------- #

test("the legal-entity registry is reachable from the menu", async ({ page }) => {
  await mockApi(page);
  await page.goto("/dashboard");

  const link = page.getByRole("link", { name: "Legal entities" });
  await expect(link).toBeVisible();
  await link.click();
  await expect(page).toHaveURL(/\/issuer$/);
});

test("reimbursements are reachable from the menu", async ({ page }) => {
  await mockApi(page);
  await page.goto("/dashboard");

  const link = page.getByRole("link", { name: "Reimbursements" });
  await expect(link).toBeVisible();
  await link.click();
  await expect(page).toHaveURL(/\/reimbursements$/);
});
