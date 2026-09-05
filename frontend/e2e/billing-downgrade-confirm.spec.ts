import { test, expect, type Page } from "@playwright/test";

/**
 * Billing plan switch: a downgrade that would disable a currently-enabled
 * module requires an explicit ConfirmDialog naming the affected module(s)
 * before `PUT /billing/plan` fires (WO-35 / R18), over the LIVE app shell
 * with `page.route` mocking — same deterministic, no-backend pattern as
 * `issue-destructive-actions.spec.ts`.
 *
 * Prior behaviour called `PUT /billing/plan` (or `POST /billing/checkout`)
 * the instant a plan card's button was clicked, silently disabling any
 * add-on module the target plan doesn't include. This spec proves the click
 * now opens a styled confirm dialog first when a module would be lost, that
 * cancelling issues NO request, that confirming calls the endpoint exactly
 * once, and that a plan change dropping nothing currently enabled still
 * proceeds with zero added friction.
 *
 * Synthetic fixtures only (master context §10): fictional org/user, no real
 * VAT/IBAN-shaped strings.
 */

const ORG = { id: "org-1", name: "Test Workspace", status: "active" };
const USER = {
  id: "user-1",
  email: "owner@test.io",
  name: "Owner",
  role: "owner",
  org_id: "org-1",
};

const TRIAL = {
  key: "trial",
  name: "Trial",
  seats: 3,
  price_eur: 0,
  modules: ["issuing", "expenses", "email_intake", "budget"],
  trial: true,
  purchasable: true,
  archive_retention_years: 3,
};
const STARTER = {
  key: "starter",
  name: "Starter",
  seats: 2,
  price_eur: 29,
  modules: ["expenses", "budget"],
  trial: false,
  purchasable: true,
  archive_retention_years: 3,
};
const PRO = {
  key: "pro",
  name: "Pro",
  seats: 10,
  price_eur: 99,
  modules: ["issuing", "expenses", "email_intake", "budget"],
  trial: false,
  purchasable: true,
  archive_retention_years: 3,
};
// WO-AD: priced, on the ladder, but the provider has no price id for it yet —
// the SPA must not offer a checkout that can only fail.
const BUSINESS = {
  key: "business",
  name: "Business",
  seats: 25,
  price_eur: 249,
  modules: ["issuing", "expenses", "email_intake", "budget"],
  trial: false,
  purchasable: false,
  archive_retention_years: 7,
};

const MODULES = [
  { key: "analytics", name: "Analytics & benchmarking", description: "", core: true, enabled: true, requires_issuer: false, ready: true },
  { key: "intake", name: "Invoice intake & OCR", description: "", core: true, enabled: true, requires_issuer: false, ready: true },
  { key: "fx", name: "FX & ECB rates", description: "", core: true, enabled: true, requires_issuer: false, ready: true },
  { key: "validation", name: "Data validation", description: "", core: true, enabled: true, requires_issuer: false, ready: true },
  { key: "issuing", name: "Invoice issuing (EU e-invoicing)", description: "", core: false, enabled: true, requires_issuer: true, ready: true },
  { key: "expenses", name: "Employee expenses", description: "", core: false, enabled: false, requires_issuer: false, ready: true },
  { key: "email_intake", name: "Email invoice intake", description: "", core: false, enabled: false, requires_issuer: false, ready: true },
  { key: "budget", name: "Monthly budgeting", description: "", core: false, enabled: false, requires_issuer: false, ready: true },
];

function billingBody(currentPlan: typeof TRIAL) {
  return {
    plan: currentPlan,
    status: "active",
    seats_used: 1,
    seats_limit: currentPlan.seats,
    available_plans: [TRIAL, STARTER, PRO, BUSINESS],
    billing_enabled: false,
    billing_provider: "none",
    has_subscription: false,
  };
}

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
    if (path === "/modules" && method === "GET") return route.fulfill(json(MODULES));
    if (path === "/billing" && method === "GET") return route.fulfill(json(billingBody(TRIAL)));
    if (path === "/billing/plan" && method === "PUT") {
      const body = req.postDataJSON() as { plan: string };
      calls.push(`plan:${body.plan}`);
      const target = [TRIAL, STARTER, PRO, BUSINESS].find((p) => p.key === body.plan)!;
      return route.fulfill(json(billingBody(target)));
    }
    if (path === "/billing/checkout" && method === "POST") {
      const body = req.postDataJSON() as { plan: string };
      calls.push(`checkout:${body.plan}`);
      return route.fulfill(json({ url: "https://checkout.example.test/session" }));
    }

    return route.fulfill(json([]));
  });

  return { calls };
}

test("switching to a plan that drops an enabled module requires an explicit confirm", async ({ page }) => {
  const { calls } = await mockApi(page);
  await page.goto("/billing");

  await expect(page.getByRole("heading", { level: 1, name: "Plan & billing" })).toBeVisible();

  // Starter drops "Invoice issuing", which is currently enabled — clicking
  // "Switch to Starter" must NOT fire the request yet.
  const starterCard = page.locator(".card", { hasText: "Starter" });
  await starterCard.getByRole("button", { name: "Switch to Starter" }).click();

  const dialog = page.getByRole("dialog", { name: /Switch to Starter/ });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("Invoice issuing (EU e-invoicing)")).toBeVisible();
  expect(calls).not.toContain("plan:starter");

  // Cancelling leaves the plan unchanged and issues no request.
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
  expect(calls).not.toContain("plan:starter");

  // Re-open and confirm — exactly one call to PUT /billing/plan, dialog closes.
  await starterCard.getByRole("button", { name: "Switch to Starter" }).click();
  await expect(page.getByRole("dialog", { name: /Switch to Starter/ })).toBeVisible();
  await page
    .getByRole("dialog", { name: /Switch to Starter/ })
    .getByRole("button", { name: "Switch to Starter" })
    .click();
  await expect(page.getByRole("dialog", { name: /Switch to Starter/ })).toBeHidden();
  expect(calls.filter((c) => c === "plan:starter")).toHaveLength(1);
});

test("switching to a plan that drops no enabled module requires no confirm", async ({ page }) => {
  const { calls } = await mockApi(page);
  await page.goto("/billing");

  await expect(page.getByRole("heading", { level: 1, name: "Plan & billing" })).toBeVisible();

  // Pro carries every module Trial does ("issuing" included) — no module is
  // lost, so the click should go straight through with no dialog.
  const proCard = page.locator(".card", { hasText: "Pro" });
  await proCard.getByRole("button", { name: "Switch to Pro" }).click();

  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect.poll(() => calls).toContain("plan:pro");
});

test("a priced plan with no provider price is shown as not yet available, never as a checkout", async ({
  page,
}) => {
  await mockApi(page);
  await page.goto("/billing");

  const card = page.locator(".card", { hasText: "Business" });
  const button = card.getByRole("button", { name: "Not yet available" });
  await expect(button).toBeVisible();
  await expect(button).toBeDisabled();
  await expect(card.getByRole("button", { name: /Switch to Business|Subscribe to Business/ })).toHaveCount(0);
});

test("each plan card states how long it keeps archived invoices", async ({ page }) => {
  // WO-AD (DECISIONS §1.B): retention is a plan attribute, so it is a line on
  // the card — the figure is the server's, not the page's.
  await mockApi(page);
  await page.goto("/billing");

  await expect(page.locator(".card", { hasText: "Business" }).getByText(/kept 7 years/)).toBeVisible();
  await expect(page.locator(".card", { hasText: "Starter" }).getByText(/kept 3 years/)).toBeVisible();
});
