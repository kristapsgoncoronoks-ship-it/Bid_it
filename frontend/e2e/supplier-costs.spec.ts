import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * Supplier cost analytics (WO-G phase 1) over the LIVE app shell, API mocked
 * via `page.route`.
 *
 * What earns its place here:
 *  - the KPI cards and the movers table render from the wire shapes, risers
 *    red / fallers green;
 *  - clicking a row loads that supplier × item's price history chart;
 *  - the unfolded-currencies caveat renders when more than one currency
 *    exists;
 *  - copy is industry-neutral (owner guard list).
 *
 * Synthetic fixtures only.
 */

const ORG = { id: "org-1", name: "Test Workspace", status: "active" };

const ROW = {
  vendor_id: "ven-1",
  vendor_name: "Supply Co",
  item: "copper pipe",
  category: "materials",
  points: 3,
  latest_price: "12.00",
  latest_date: "2026-08-20",
  trailing_avg: "10.00",
  pct_change: "20.0",
};

async function open(page: Page, opts: { onHistory?: (q: URLSearchParams) => void } = {}) {
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));

  const json = (body: unknown, code = 200) => ({
    status: code,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

  await page.route("**/api/v1/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const p = url.pathname.replace(/^.*\/api\/v1/, "");

    if (p === "/auth/me")
      return route.fulfill(
        json({
          user: {
            id: "user-1",
            email: "someone@test.io",
            name: "Test User",
            role: "owner",
            org_id: "org-1",
            is_platform_admin: false,
          },
          organization: ORG,
        }),
      );
    if (p === "/auth/organizations") return route.fulfill(json([ORG]));
    if (p === "/modules") return route.fulfill(json([]));

    if (p === "/analytics/supplier-costs/kpis")
      return route.fulfill(
        json({
          currency: "EUR",
          available_currencies: ["EUR", "USD"],
          window_days: 365,
          suppliers: 2,
          tracked_items: 3,
          risers: 2,
          fallers: 1,
          biggest_mover: ROW,
        }),
      );
    if (p === "/analytics/supplier-costs/changes")
      return route.fulfill(
        json({
          currency: "EUR",
          available_currencies: ["EUR", "USD"],
          window_days: 365,
          total_tracked: 3,
          rows: [
            ROW,
            { ...ROW, item: "sealant", latest_price: "7.60", trailing_avg: "8.00", pct_change: "-5.0" },
          ],
        }),
      );
    if (p === "/analytics/supplier-costs/history") {
      opts.onHistory?.(url.searchParams);
      return route.fulfill(
        json({
          currency: "EUR",
          vendor_id: "ven-1",
          item: "copper pipe",
          series: [
            { month: "2026-06", avg_price: "10.00", quantity: "4", spend: "40.00", points: 1 },
            { month: "2026-08", avg_price: "12.00", quantity: "4", spend: "48.00", points: 2 },
          ],
        }),
      );
    }

    return route.fulfill(json({ items: [], total: 0 }));
  });

  await page.goto("/supplier-costs");
}

test("KPI cards and the movers table render, movers colour-coded", async ({ page }) => {
  await open(page);
  await expect(page.getByRole("heading", { name: "Supplier costs" })).toBeVisible();
  await expect(page.getByText("Suppliers tracked")).toBeVisible();
  await expect(page.getByText("2 / 1")).toBeVisible();

  const riser = page.locator("td", { hasText: "+20.0%" });
  await expect(riser).toBeVisible();
  await expect(riser).toHaveClass(/text-rose-600/);
  const faller = page.locator("td", { hasText: "-5.0%" });
  await expect(faller).toHaveClass(/text-emerald-600/);

  await expect(page.getByText("are not folded into these EUR figures")).toBeVisible();
});

test("clicking a mover loads that item's price history", async ({ page }) => {
  const queries: URLSearchParams[] = [];
  await open(page, { onHistory: (q) => queries.push(q) });

  await page.locator("tr", { hasText: "copper pipe" }).first().click();
  await expect(page.getByRole("heading", { name: "Price history — copper pipe" })).toBeVisible();
  await expect.poll(() => queries.length).toBeGreaterThan(0);
  expect(queries[0].get("vendor_id")).toBe("ven-1");
  expect(queries[0].get("item")).toBe("copper pipe");
});

test("the supplier-costs copy is industry-neutral", async ({ page }) => {
  await open(page);
  await expect(page.getByRole("heading", { name: "Supplier costs" })).toBeVisible();
  const text = (await page.locator("body").innerText()).toLowerCase();
  for (const word of ["cargo", "fuel", "vehicle", "driver", "truck", "site crew"]) {
    expect(text).not.toContain(word);
  }
});
