import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * PROD-001 (audit 2026-09-05) — a suspended workspace renders ONE destination.
 *
 * A declined subscription payment sets the organisation `suspended`. Before the
 * fix the identity read itself 401'd, the SPA dropped the token, and the owner
 * landed on the sign-in page with "could not validate credentials" — locked out
 * of the screen that takes the card. Now `/auth/me` and `/billing` answer for a
 * suspended org while every data route still 401s with
 * `code: organization_suspended`.
 *
 * What earns its place here:
 *  - the OWNER is routed to Plan & billing from anywhere, sees the reason, sees
 *    the plans, and the nav holds nothing else (no wall of dead links);
 *  - a non-owner member sees the reason and who can act, no billing, no nav;
 *  - a data-route 401 carrying the suspension code does NOT log the user out.
 *
 * Live app shell, API mocked via page.route. Synthetic fixtures only.
 */

const ORG = { id: "org-1", name: "Haulage Co", status: "suspended" };

const PLAN = (key: string, name: string, price: number | null) => ({
  key,
  name,
  seats: 5,
  price_eur: price,
  modules: ["issuing"],
  trial: false,
  purchasable: price !== null,
  archive_retention_years: 3,
});

async function open(page: Page, role: "owner" | "user", path = "/") {
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));
  const json = (body: unknown, code = 200) => ({
    status: code,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
  const dataCalls: string[] = [];
  await page.route("**/api/v1/**", async (route: Route) => {
    const p = new URL(route.request().url()).pathname.replace(/^.*\/api\/v1/, "");
    if (p === "/auth/me")
      return route.fulfill(
        json({
          user: { id: "u1", email: "owner@haulage.example", name: "Owner", role, org_id: "org-1", is_platform_admin: false },
          organization: ORG,
        }),
      );
    if (p === "/auth/organizations") return route.fulfill(json([ORG]));
    if (p === "/modules") return route.fulfill(json([]));
    if (p === "/billing") {
      if (role !== "owner") return route.fulfill(json({ detail: "Forbidden" }, 403));
      return route.fulfill(
        json({
          plan: PLAN("pro", "Team", 99),
          status: "suspended",
          seats_used: 3,
          seats_limit: 5,
          available_plans: [PLAN("free", "Free", 0), PLAN("starter", "Starter", 39), PLAN("pro", "Team", 99)],
          billing_enabled: true,
          billing_provider: "stripe",
          has_subscription: true,
        }),
      );
    }
    // Every data route: the suspension 401, exactly as the server sends it.
    dataCalls.push(p);
    return route.fulfill(json({ detail: "Could not validate credentials", code: "organization_suspended" }, 401));
  });
  await page.goto(path);
  return dataCalls;
}

test("the owner is routed to Plan & billing and sees why", async ({ page }) => {
  await open(page, "owner", "/invoices");
  await expect(page).toHaveURL(/\/billing$/);
  await expect(page.getByRole("alert").first()).toContainText("This workspace is suspended");
  await expect(page.getByRole("heading", { name: "Plan & billing" })).toBeVisible();
  // The reason, in the page as well as the banner, and the plans to act on.
  await expect(page.getByText("The last subscription payment did not go through")).toBeVisible();
  await expect(page.getByText("Starter", { exact: true })).toBeVisible();
  // The nav holds nothing but Billing — no wall of dead links.
  const nav = page.getByRole("navigation", { name: "Primary" });
  await expect(nav.getByRole("link")).toHaveCount(1);
  await expect(nav.getByRole("link", { name: "Billing" })).toBeVisible();
  // And the token survived: we are on /billing, not /login.
  expect(await page.evaluate(() => localStorage.getItem("invoiceiq_token"))).toBe("e2e-token");
});

test("a non-owner member sees the reason and who can act, and nothing else", async ({ page }) => {
  await open(page, "user", "/");
  await expect(page.getByRole("heading", { name: "This workspace is suspended" })).toBeVisible();
  await expect(page.getByText("Only the workspace owner can restore access")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Plan & billing" })).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("link")).toHaveCount(0);
  expect(await page.evaluate(() => localStorage.getItem("invoiceiq_token"))).toBe("e2e-token");
});
