import { test, expect, type Page } from "@playwright/test";

/**
 * Reference integration R2 → R4 (review finding Product P-2, then the R4
 * review's U-1/U-2/Q-2/Q-3): the Stripe webhook is applied by the WORKER from
 * a durable job, so the page the provider returns to
 * (`/billing?checkout=success`) may still show the old plan for a few seconds.
 * The page must say so, keep the plan buttons disabled (a second click would
 * start a second Checkout), poll until the plan the owner BOUGHT is current,
 * drop the query string once settled, and — if the wait runs out — say so
 * truthfully instead of quietly re-enabling "Subscribe".
 *
 * Live app shell, API mocked via page.route. Synthetic fixtures only.
 */

const ORG = { id: "org-1", name: "Haulage Co", status: "active" };
const USER = { id: "user-1", email: "owner@haulage.example", name: "Owner", role: "owner", org_id: "org-1" };

const TRIAL = {
  key: "trial",
  name: "Trial",
  seats: 3,
  price_eur: 0,
  modules: ["issuing"],
  trial: true,
  purchasable: true,
  archive_retention_years: 3,
};
const PRO = {
  key: "pro",
  name: "Pro",
  seats: 10,
  price_eur: 99,
  modules: ["issuing"],
  trial: false,
  purchasable: true,
  archive_retention_years: 3,
};

function billingBody(currentPlan: typeof TRIAL) {
  return {
    plan: currentPlan,
    status: "active",
    seats_used: 1,
    seats_limit: currentPlan.seats,
    available_plans: [TRIAL, PRO],
    billing_enabled: true,
    billing_provider: "stripe",
    has_subscription: currentPlan.key !== "trial",
  };
}

const json = (body: unknown, status = 200) => ({
  status,
  contentType: "application/json",
  body: JSON.stringify(body),
});

interface MockOpts {
  /** The first `planAfter` reads of /billing still show the OLD plan. */
  planAfter: number;
  /** The plan key the owner chose before leaving for Checkout (sessionStorage). */
  target?: string | null;
  /** Answer this read of /billing (1-based) with a 500 once. */
  failRead?: number;
}

async function mockApi(page: Page, opts: MockOpts): Promise<{ billingReads: number; checkouts: number; me: number }> {
  const target = opts.target === undefined ? "pro" : opts.target;
  await page.addInitScript(
    ({ t }) => {
      localStorage.setItem("invoiceiq_token", "e2e-token");
      if (t !== null && sessionStorage.getItem("invoiceiq_checkout_target") === null && !sessionStorage.getItem("e2e-target-seeded")) {
        sessionStorage.setItem("invoiceiq_checkout_target", t);
        sessionStorage.setItem("e2e-target-seeded", "1");
      }
    },
    { t: target },
  );
  const counters = { billingReads: 0, checkouts: 0, me: 0 };
  await page.route("**/api/v1/**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace(/^.*\/api\/v1/, "");
    if (path === "/auth/me") {
      counters.me += 1;
      return route.fulfill(json({ user: USER, organization: ORG }));
    }
    if (path === "/auth/organizations") return route.fulfill(json([{ org_id: "org-1", name: ORG.name, role: "owner", current: true }]));
    if (path === "/modules") return route.fulfill(json([]));
    if (path === "/billing" && req.method() === "GET") {
      counters.billingReads += 1;
      if (opts.failRead === counters.billingReads) return route.fulfill(json({ detail: "temporarily unavailable" }, 500));
      // The worker applies the webhook job "after a few seconds": the first
      // `planAfter` reads still show the old plan.
      return route.fulfill(json(billingBody(counters.billingReads > opts.planAfter ? PRO : TRIAL)));
    }
    if (path === "/billing/checkout") {
      counters.checkouts += 1;
      return route.fulfill(json({ url: "https://checkout.example/session" }));
    }
    return route.fulfill(json([]));
  });
  return counters;
}

test("after Checkout the page says it is activating, blocks a second subscribe, and settles on the plan that was bought", async ({ page }) => {
  const counters = await mockApi(page, { planAfter: 2 });
  await page.goto("/billing?checkout=success");

  const notice = page.getByRole("status");
  await expect(notice).toContainText("activating your plan");
  const subscribe = page.getByRole("button", { name: "Subscribe to Pro" });
  await expect(subscribe).toBeVisible();
  await expect(subscribe).toBeDisabled();
  await subscribe.click({ force: true, trial: false }).catch(() => undefined);

  // The worker lands the plan; the poll sees it, the notice goes, Pro is
  // current, the query string is gone so a reload does not wait again, and
  // the identity was re-read (a suspended workspace reactivating).
  const meBefore = counters.me;
  // Pro is current exactly when Trial is offered as a switch (the trial card
  // also says "Current plan" before the plan lands — not an anchor).
  await expect(page.getByRole("button", { name: "Switch to Trial" })).toBeVisible({ timeout: 15_000 });
  await expect(notice).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Subscribe to Pro" })).toHaveCount(0);
  await expect(page).toHaveURL(/\/billing$/);
  expect(counters.billingReads).toBeGreaterThan(2);
  expect(counters.checkouts).toBe(0); // nothing subscribed twice, before or after settling
  await expect.poll(() => counters.me).toBeGreaterThan(meBefore);
});

test("when the provider already applied the plan before redirecting, nothing is shown and nothing polls", async ({ page }) => {
  // EveryPay always; Stripe when the webhook beats the browser (the common case).
  const counters = await mockApi(page, { planAfter: 0 });
  await page.goto("/billing?checkout=success");

  await expect(page.getByRole("button", { name: "Switch to Trial" })).toBeVisible();
  await expect(page).toHaveURL(/\/billing$/);
  await expect(page.getByRole("button", { name: "Manage billing" })).toBeEnabled();
  await expect(page.getByRole("status")).toHaveCount(0);
  await page.waitForTimeout(2_500);
  expect(counters.billingReads).toBe(1);
});

test("a failed read while activating does not end the wait", async ({ page }) => {
  const counters = await mockApi(page, { planAfter: 2, failRead: 2 });
  await page.goto("/billing?checkout=success");
  await expect(page.getByRole("status")).toContainText("activating your plan");
  await expect(page.getByRole("button", { name: "Switch to Trial" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("status")).toHaveCount(0);
  expect(counters.billingReads).toBeGreaterThanOrEqual(3);
  expect(counters.checkouts).toBe(0);
});

test("when the wait runs out the page says so and still refuses a second subscribe to the bought plan", async ({ page }) => {
  await page.clock.install();
  const counters = await mockApi(page, { planAfter: 999 });
  await page.goto("/billing?checkout=success");
  await expect(page.getByRole("status")).toContainText("activating your plan");

  await page.clock.runFor(90_000);

  const alert = page.getByRole("alert");
  await expect(alert).toContainText("has not updated yet");
  await expect(alert).toContainText("Do not subscribe again");
  await expect(alert).toContainText(ORG.id);
  await expect(page.getByRole("status")).toHaveCount(0);
  const subscribe = page.getByRole("button", { name: "Subscribe to Pro" });
  await expect(subscribe).toBeVisible();
  await expect(subscribe).toBeDisabled();
  await subscribe.click({ force: true, trial: false }).catch(() => undefined);
  expect(counters.checkouts).toBe(0);
  // The query string is kept on purpose: a refresh starts a new wait.
  await expect(page).toHaveURL(/checkout=success/);
});

test("an ordinary visit is not told it is activating and does not poll", async ({ page }) => {
  const counters = await mockApi(page, { planAfter: 999, target: null });
  await page.goto("/billing");
  await expect(page.getByRole("button", { name: "Subscribe to Pro" })).toBeEnabled();
  await expect(page.getByText("activating your plan")).toHaveCount(0);
  await page.waitForTimeout(2500);
  expect(counters.billingReads).toBe(1);
});
