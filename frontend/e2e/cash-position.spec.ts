import { test, expect, type Page } from "@playwright/test";

/**
 * Cash-position page honesty (WO-18 / I1.3) over the LIVE app shell, API
 * mocked via `page.route` — deterministic, no backend. `net_position` is
 * receivables − payables (a working-capital gap, not a bank balance — no
 * bank-account entity exists, see `backend/app/services/cash_position.py`)
 * and the cash-flow trend is strictly backward-looking (`cash_flow.monthly()`
 * buckets settled payments into the trailing window, no projection). This
 * spec asserts the page discloses both honestly instead of reading like a
 * bank-balance summary / a forecast.
 *
 * Synthetic fixtures only (master context §10): fictional figures.
 */

const ORG = { id: "org-1", name: "Test Workspace", status: "active" };
const USER = {
  id: "user-1",
  email: "owner@test.io",
  name: "Owner",
  role: "owner",
  org_id: "org-1",
};

const AR = {
  currency: "EUR",
  outstanding: "3210.00",
  overdue: "1210.00",
  avg_days_to_pay: 21.5,
  aging: [{ label: "0-30", count: 2, outstanding: "2000.00" }],
};

const AP = {
  outstanding: "1730.00",
  overdue: "480.00",
  count: 4,
  scheduled: 1,
  in_run: 1,
};

const RECON = { unmatched: 2, matched: 10, ignored: 0, unmatched_amount: "150.00" };

const AP_AGING = {
  currency: "EUR",
  due_soon_count: 0,
  due_soon_amount: "0.00",
  overdue_count: 0,
  overdue_amount: "0.00",
  other_currencies: [] as string[],
  items: [] as unknown[],
};

const CASH_FLOW = [
  { period: "2026-06", inflow: "5000.00", outflow: "3200.00", net: "1800.00" },
  { period: "2026-07", inflow: "4200.00", outflow: "3900.00", net: "300.00" },
];

async function mockApi(
  page: Page,
  opts: { net: string; cashFlow: unknown[] },
): Promise<void> {
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));

  const json = (body: unknown, status = 200) => ({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^.*\/api\/v1/, "");

    if (path === "/auth/me") return route.fulfill(json({ user: USER, organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/modules") return route.fulfill(json([]));
    if (path === "/analytics/cash-position")
      return route.fulfill(
        json({
          currency: "EUR",
          receivables: AR,
          payables: AP,
          reconciliation: RECON,
          net_position: opts.net,
        }),
      );
    if (path === "/analytics/ap-aging") return route.fulfill(json(AP_AGING));
    if (path === "/analytics/cash-flow") return route.fulfill(json(opts.cashFlow));
    return route.fulfill(json({}));
  });
}

test("headline reads working-capital gap, not net position, and discloses it is not a bank balance", async ({
  page,
}) => {
  await mockApi(page, { net: "1480.00", cashFlow: CASH_FLOW });
  await page.goto("/cash-position");

  await expect(page.getByRole("heading", { level: 1, name: "Cash position" })).toBeVisible();

  const gap = page.getByText("Working-capital gap", { exact: true });
  await expect(gap).toBeVisible();
  // The old misleading label must be gone, not just supplemented.
  await expect(page.getByText("Net position", { exact: true })).toHaveCount(0);

  const gapCard = gap.locator("..");
  await expect(gapCard).toContainText("not a bank balance");
  await expect(gapCard).toContainText("receivables exceed payables");
});

test("negative net position still discloses the same caveat", async ({ page }) => {
  await mockApi(page, { net: "-250.00", cashFlow: CASH_FLOW });
  await page.goto("/cash-position");

  const gap = page.getByText("Working-capital gap", { exact: true });
  const gapCard = gap.locator("..");
  await expect(gapCard).toContainText("payables exceed receivables");
  await expect(gapCard).toContainText("not a bank balance");
});

test("cash-flow card discloses it is historical, not a forecast", async ({ page }) => {
  await mockApi(page, { net: "1480.00", cashFlow: CASH_FLOW });
  await page.goto("/cash-position");

  const heading = page.getByText(/Cash flow/, { exact: false }).first();
  await expect(heading).toBeVisible();
  await expect(heading).toContainText("historical");

  const caption = page.getByText(/look back, not a forecast/i);
  await expect(caption).toBeVisible();
  await expect(page.getByText(/forecast/i)).toHaveCount(1); // only in the "not a forecast" caption
  await expect(page.getByText(/projection/i)).toHaveCount(0);
});

test("empty cash-flow series renders no chart card and no dangling caption", async ({ page }) => {
  await mockApi(page, { net: "1480.00", cashFlow: [] });
  await page.goto("/cash-position");

  await expect(page.getByText(/Cash flow/, { exact: false })).toHaveCount(0);
  await expect(page.getByText(/look back, not a forecast/i)).toHaveCount(0);
});
