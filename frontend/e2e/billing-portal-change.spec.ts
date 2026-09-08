import { test, expect, type Page } from "@playwright/test";

/**
 * BE-022 (audit 2026-09-05, found by the R4 review): a workspace that already
 * holds a Stripe subscription must never start a SECOND Checkout — that opens
 * a second subscription at the provider. The server refuses it (409); the
 * page never offers it: a paid plan's button for a subscriber reads "Change to
 * … via Manage billing" and opens the Customer Portal. A workspace without a
 * subscription still subscribes through Checkout, and a redirect provider
 * (EveryPay, no portal, no subscription object) still opens a hosted payment.
 *
 * Live app shell, API mocked via page.route. Synthetic fixtures only.
 */

const ORG = { id: "org-1", name: "Haulage Co", status: "active" };
const USER = { id: "user-1", email: "owner@haulage.example", name: "Owner", role: "owner", org_id: "org-1" };

const plan = (key: string, name: string, price_eur: number) => ({
  key,
  name,
  seats: 10,
  price_eur,
  modules: ["issuing"],
  trial: false,
  purchasable: true,
  archive_retention_years: 3,
});
const FREE = plan("free", "Free", 0);
const PRO = plan("pro", "Pro", 99);
const BUSINESS = plan("business", "Business", 199);

const json = (body: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });

interface Opts {
  current: typeof PRO;
  provider: "stripe" | "everypay";
  hasSubscription: boolean;
  status?: string;
  /** Enabled non-core modules the workspace uses (drives the R18 confirm). */
  modules?: { key: string; name: string; core: boolean; enabled: boolean }[];
}

async function mockApi(page: Page, opts: Opts) {
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));
  const calls = { checkout: 0, portal: 0, plan: 0 };
  await page.route("**/api/v1/**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace(/^.*\/api\/v1/, "");
    if (path === "/auth/me") return route.fulfill(json({ user: USER, organization: { ...ORG, status: opts.status ?? "active" } }));
    if (path === "/auth/organizations") return route.fulfill(json([{ org_id: "org-1", name: ORG.name, role: "owner", current: true }]));
    if (path === "/modules") return route.fulfill(json(opts.modules ?? []));
    if (path === "/billing" && req.method() === "GET") {
      return route.fulfill(
        json({
          plan: opts.current,
          status: opts.status ?? "active",
          seats_used: 1,
          seats_limit: opts.current.seats,
          available_plans: [FREE, PRO, BUSINESS],
          billing_enabled: true,
          billing_provider: opts.provider,
          has_subscription: opts.hasSubscription,
        }),
      );
    }
    if (path === "/billing/checkout") {
      calls.checkout += 1;
      return route.fulfill(json({ url: "https://checkout.example/session" }));
    }
    if (path === "/billing/portal") {
      calls.portal += 1;
      return route.fulfill(json({ url: "https://portal.example/session" }));
    }
    if (path === "/billing/plan") {
      calls.plan += 1;
      return route.fulfill(json({ detail: "not expected here" }, 409));
    }
    return route.fulfill(json([]));
  });
  return calls;
}

test("a Stripe subscriber changes plan through the Portal, never through a second Checkout", async ({ page }) => {
  const calls = await mockApi(page, { current: PRO, provider: "stripe", hasSubscription: true });
  await page.goto("/billing");
  const change = page.getByRole("button", { name: "Change to Business via Manage billing" });
  await expect(change).toBeEnabled();
  await expect(page.getByRole("button", { name: "Manage billing", exact: true })).toBeEnabled();
  await expect(page.getByRole("button", { name: "Subscribe to Business" })).toHaveCount(0);
  // The Portal redirect leaves the page; stop navigation so the call is observable.
  await page.route("https://portal.example/**", (r) => r.fulfill({ status: 200, body: "portal" }));
  await change.click();
  await expect.poll(() => calls.portal).toBe(1);
  expect(calls.checkout).toBe(0);
  expect(calls.plan).toBe(0);
});

test("a suspended subscriber is sent to Manage billing for the payment method", async ({ page }) => {
  await mockApi(page, { current: PRO, provider: "stripe", hasSubscription: true, status: "suspended" });
  await page.goto("/billing");
  // The shell's own banner is the first alert; the page's reason is the one
  // that names the way out.
  await expect(page.getByRole("alert").filter({ hasText: "through Manage billing" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Manage billing", exact: true })).toBeEnabled();
  await expect(page.getByRole("button", { name: "Change to Business via Manage billing" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "Subscribe to Business" })).toHaveCount(0);
});

test("a workspace without a subscription still subscribes through Checkout", async ({ page }) => {
  const calls = await mockApi(page, { current: FREE, provider: "stripe", hasSubscription: false });
  await page.goto("/billing");
  const subscribe = page.getByRole("button", { name: "Subscribe to Pro" });
  await expect(subscribe).toBeEnabled();
  await expect(page.getByRole("button", { name: "Change to Pro via Manage billing" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Manage billing", exact: true })).toHaveCount(0);
  await page.route("https://checkout.example/**", (r) => r.fulfill({ status: 200, body: "checkout" }));
  await subscribe.click();
  await expect.poll(() => calls.checkout).toBe(1);
  expect(calls.portal).toBe(0);
});

test("an EveryPay workspace with a stored card still opens a hosted payment (no portal exists)", async ({ page }) => {
  const calls = await mockApi(page, { current: PRO, provider: "everypay", hasSubscription: true });
  await page.goto("/billing");
  const subscribe = page.getByRole("button", { name: "Subscribe to Business" });
  await expect(subscribe).toBeEnabled();
  await expect(page.getByRole("button", { name: "Manage billing", exact: true })).toHaveCount(0);
  await page.route("https://checkout.example/**", (r) => r.fulfill({ status: 200, body: "checkout" }));
  await subscribe.click();
  await expect.poll(() => calls.checkout).toBe(1);
  expect(calls.portal).toBe(0);
});

test("a subscriber's Free card cancels through the Portal — never the in-app switch that leaves the charge running", async ({ page }) => {
  // BE-023: `PUT /billing/plan {free}` cannot cancel a Stripe subscription; the
  // workspace would lose its modules while still being charged, and the next
  // renewal webhook would put the paid plan back.
  const calls = await mockApi(page, { current: PRO, provider: "stripe", hasSubscription: true });
  await page.goto("/billing");
  const cancel = page.getByRole("button", { name: "Cancel subscription via Manage billing" });
  await expect(cancel).toBeEnabled();
  await expect(page.getByRole("button", { name: "Switch to Free" })).toHaveCount(0);
  await expect(page.getByText("a second subscription is never started")).toBeVisible();
  await page.route("https://portal.example/**", (r) => r.fulfill({ status: 200, body: "portal" }));
  await cancel.click();
  await expect.poll(() => calls.portal).toBe(1);
  expect(calls.plan).toBe(0);
  expect(calls.checkout).toBe(0);
});

test("the module-loss confirmation is honest when the change happens in the Portal", async ({ page }) => {
  // R18's dialog used to say "Switch to X? … switching will disable it" while
  // confirming only opened the Portal.
  const calls = await mockApi(page, {
    current: BUSINESS,
    provider: "stripe",
    hasSubscription: true,
    modules: [{ key: "transport", name: "Transport", core: false, enabled: true }],
  });
  await page.goto("/billing");
  await page.getByRole("button", { name: "Change to Pro via Manage billing" }).click();
  const dialog = page.getByRole("dialog", { name: /Change to Pro in Manage billing\?/ });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("Transport");
  await expect(dialog).toContainText("will be disabled when the new plan is applied");
  expect(calls.portal).toBe(0); // nothing happened yet
  await page.route("https://portal.example/**", (r) => r.fulfill({ status: 200, body: "portal" }));
  await dialog.getByRole("button", { name: "Continue to Manage billing" }).click();
  await expect.poll(() => calls.portal).toBe(1);
  expect(calls.checkout).toBe(0);
});
