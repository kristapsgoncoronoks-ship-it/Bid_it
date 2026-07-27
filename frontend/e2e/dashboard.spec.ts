import { test, expect, type Page } from "@playwright/test";

/**
 * The composed home dashboard (WO-16 / I1.1) over the LIVE app shell, API
 * mocked via `page.route` — deterministic, no backend. Three states:
 * a full payload (every card + links to filtered worklists), a reduced
 * payload (server nulled the gated sections → no dead cards, no error),
 * and the all-clear positive empty state.
 *
 * Synthetic fixtures only (master context §10): fictional names and figures.
 */

const ORG = { id: "org-1", name: "Test Workspace", status: "active" };
const USER = {
  id: "user-1",
  email: "owner@test.io",
  name: "Owner",
  role: "owner",
  org_id: "org-1",
};

const FULL_DASHBOARD = {
  as_of: "2026-07-27",
  approvals: {
    invoices: [
      {
        invoice_id: "inv-77",
        invoice_number: "INV-2026-077",
        vendor_name: "Fictional Fuels OÜ",
        total: "480.00",
        currency: "EUR",
      },
    ],
    invoice_count: 1,
    expense_reports: 2,
    payment_runs: 1,
    vendor_changes: 1,
    total: 5,
  },
  captures: { pending: 3, low_confidence_fields: 4 },
  payables: {
    currency: "EUR",
    due_soon_count: 2,
    due_soon_amount: "1250.00",
    overdue_count: 1,
    overdue_amount: "480.00",
    other_currencies: ["SEK"],
  },
  receivables: {
    currency: "EUR",
    outstanding: "3210.00",
    overdue: "1210.00",
    avg_days_to_pay: 21.5,
  },
  cash: {
    currency: "EUR",
    receivables_outstanding: "3210.00",
    payables_outstanding: "1730.00",
    net_position: "1480.00",
  },
};

// What an EMPLOYEE sees: only the captures section (server nulls the rest).
const REDUCED_DASHBOARD = {
  as_of: "2026-07-27",
  approvals: null,
  captures: { pending: 0, low_confidence_fields: 0 },
  payables: null,
  receivables: null,
  cash: null,
};

const ALL_CLEAR = {
  ...FULL_DASHBOARD,
  approvals: { ...FULL_DASHBOARD.approvals, invoices: [], invoice_count: 0, expense_reports: 0, payment_runs: 0, vendor_changes: 0, total: 0 },
  captures: { pending: 0, low_confidence_fields: 0 },
  payables: { ...FULL_DASHBOARD.payables, due_soon_count: 0, due_soon_amount: "0.00", overdue_count: 0, overdue_amount: "0.00", other_currencies: [] },
  receivables: { ...FULL_DASHBOARD.receivables, overdue: "0.00" },
};

async function mockApi(page: Page, dashboard: unknown): Promise<void> {
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
    if (path === "/dashboard") return route.fulfill(json(dashboard));
    if (path === "/invoices")
      return route.fulfill(json({ items: [], total: 0, page: 1, page_size: 20 }));
    // Spend-snapshot analytics — empty datasets render their empty branches.
    if (path.startsWith("/analytics/")) return route.fulfill(json([]));
    return route.fulfill(json([]));
  });
}

test("full dashboard renders every tile with its worklist link", async ({ page }) => {
  await mockApi(page, FULL_DASHBOARD);
  await page.goto("/");

  await expect(page.getByRole("heading", { level: 1, name: "Today" })).toBeVisible();

  // Attention tiles link to their filtered worklists.
  const approvals = page.getByRole("link", { name: /Waiting on you.*5/s }).first();
  await expect(approvals).toHaveAttribute("href", "/invoices?workflow_state=in_approval");
  await expect(page.getByRole("link", { name: /Captures to review/s })).toHaveAttribute(
    "href",
    "/captures",
  );
  await expect(page.getByRole("link", { name: /Overdue payables/s })).toHaveAttribute(
    "href",
    "/cash-position",
  );
  await expect(page.getByRole("link", { name: /Due within 7 days/s })).toHaveAttribute(
    "href",
    "/cash-position",
  );
  await expect(page.getByRole("link", { name: /Overdue receivables/s })).toHaveAttribute(
    "href",
    "/issue/reports",
  );

  // The cash tile carries the honest working-capital label — not a bank balance.
  const cashTile = page.getByRole("link", { name: /Working-capital gap/s });
  await expect(cashTile).toContainText("not a bank balance");
  await expect(cashTile).toHaveAttribute("href", "/cash-position");

  // The approvals card lists the pending invoice with a decide link, plus the
  // per-surface counters linking to their pages.
  await expect(page.getByText("INV-2026-077")).toBeVisible();
  await expect(page.getByRole("link", { name: "Decide →" })).toHaveAttribute(
    "href",
    "/invoices/inv-77",
  );
  await expect(page.getByRole("link", { name: /2 Expense reports/ })).toHaveAttribute(
    "href",
    "/expenses",
  );
  await expect(page.getByRole("link", { name: /1 Payment runs to check/ })).toHaveAttribute(
    "href",
    "/payment-runs",
  );
  await expect(page.getByRole("link", { name: /1 Supplier detail changes/ })).toHaveAttribute(
    "href",
    "/vendors",
  );

  // Deep link actually lands on the filtered worklist with the chip.
  await approvals.click();
  await expect(page).toHaveURL(/\/invoices\?workflow_state=in_approval/);
  await expect(page.getByText("Awaiting approval")).toBeVisible();
});

test("reduced payload renders no gated cards and no error", async ({ page }) => {
  await mockApi(page, REDUCED_DASHBOARD);
  await page.goto("/");

  await expect(page.getByRole("heading", { level: 1, name: "Today" })).toBeVisible();
  // Only the captures tile renders; every nulled section leaves nothing behind.
  await expect(page.getByRole("link", { name: /Captures to review/s })).toBeVisible();
  await expect(page.getByText("Waiting on you")).toHaveCount(0);
  await expect(page.getByText("Working-capital gap")).toHaveCount(0);
  await expect(page.getByText("Overdue payables")).toHaveCount(0);
  await expect(page.getByText("Spend snapshot")).toHaveCount(0);
  await expect(page.getByText(/Couldn’t load/)).toHaveCount(0);
  // All counts are zero → the positive all-clear state shows.
  await expect(page.getByText("Nothing needs you right now")).toBeVisible();
});

test("all-clear payload shows the positive empty state", async ({ page }) => {
  await mockApi(page, ALL_CLEAR);
  await page.goto("/");
  await expect(page.getByText("Nothing needs you right now")).toBeVisible();
  // Zero-count approvals render no worklist card.
  await expect(page.getByText("All invoices awaiting approval")).toHaveCount(0);
});
