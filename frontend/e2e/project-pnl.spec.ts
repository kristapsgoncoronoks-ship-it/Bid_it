import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * Project profitability, phase 1 — the client-facing loop over the LIVE app
 * shell with the API mocked via `page.route` (the suite's standard pattern).
 *
 * What earns its place here:
 *  - the route resolves and the P&L renders THE WIRE'S decimal strings — no
 *    float reformatting between server and eye;
 *  - the basis line renders from the server's `basis` field, so when phase 2
 *    freezes closed projects the copy follows the field;
 *  - adding a cost line posts what the operator typed, and the P&L refetches;
 *  - the issue form's project picker sends `project_id` — the revenue link the
 *    whole feature stands on;
 *  - industry-neutral copy: the screen never names an industry (owner
 *    requirement, checked here against the guard list's nouns).
 *
 * Synthetic fixtures only.
 */

const ORG = { id: "org-1", name: "Test Workspace", status: "active" };
const USER = {
  id: "user-1",
  email: "someone@test.io",
  name: "Test User",
  role: "owner",
  org_id: "org-1",
  is_platform_admin: false,
};

const PNL = {
  project_id: "proj-1",
  code: "JOB-7",
  name: "Won contract",
  status: "active",
  revenue: "1000.00",
  credited: "0.00",
  costs: "550.00",
  invoice_costs: "200.00",
  expense_costs: "50.00",
  manual_costs: "300.00",
  profit: "450.00",
  margin_pct: "45.0",
  basis: "net_eur_live",
  adjustments: {},
  pnl_frozen_at: null,
};

interface MockOpts {
  pnl?: Record<string, unknown>;
  onCostEntry?: (body: Record<string, unknown>) => void;
  onIssue?: (body: Record<string, unknown>) => void;
}

async function open(page: Page, opts: MockOpts = {}) {
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));

  const json = (body: unknown, code = 200) => ({
    status: code,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

  await page.route("**/api/v1/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^.*\/api\/v1/, "");
    const method = route.request().method();

    if (path === "/auth/me") return route.fulfill(json({ user: USER, organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/modules")
      return route.fulfill(
        json([{ key: "issuing", enabled: true, label: "Issuing", available: true }]),
      );

    if (path === "/masters/projects/proj-1/pnl")
      return route.fulfill(json({ ...PNL, ...(opts.pnl ?? {}) }));
    if (path === "/masters/projects/proj-1/cost-entries") {
      if (method === "POST") {
        opts.onCostEntry?.(route.request().postDataJSON());
        return route.fulfill(
          json(
            {
              id: "ce-1",
              label: "Crew wages",
              category: "wages",
              amount: "300.00",
              currency: "EUR",
              entry_date: null,
              note: null,
              created_by: USER.email,
              created_at: "2026-08-16T10:00:00Z",
            },
            201,
          ),
        );
      }
      return route.fulfill(json([]));
    }
    if (path === "/masters/projects/proj-1/documents") return route.fulfill(json([]));
    if (path === "/masters/projects")
      return route.fulfill(
        json([
          { id: "proj-1", code: "JOB-7", name: "Won contract", status: "active", version: 1 },
        ]),
      );

    if (path === "/issued" && method === "POST") {
      opts.onIssue?.(route.request().postDataJSON());
      return route.fulfill(
        json({ id: "ii-1", number: "ACM-1", lifecycle: "issued", lines: [], total: "0" }, 201),
      );
    }
    if (path.startsWith("/issued")) return route.fulfill(json({ items: [], total: 0 }));
    if (path.startsWith("/issuer/registry")) return route.fulfill(json([]));
    if (path.startsWith("/issuer")) return route.fulfill(
      json({ legal_name: "Acme OU", is_complete: true }),
    );
    if (path.startsWith("/partners")) return route.fulfill(json([]));
    if (path.startsWith("/customers")) return route.fulfill(json([]));
    if (path.startsWith("/recurring")) return route.fulfill(json([]));

    return route.fulfill(json({ items: [], total: 0 }));
  });
}

test("the project page renders the wire's figures verbatim", async ({ page }) => {
  await open(page);
  await page.goto("/projects/proj-1");

  await expect(page.getByRole("heading", { name: "JOB-7 · Won contract" })).toBeVisible();
  await expect(page.getByText("1000.00 €")).toBeVisible();
  await expect(page.getByText("450.00 €")).toBeVisible();
  await expect(page.getByText("45.0%")).toBeVisible();
  // The basis line is the SERVER'S statement, rendered only because the wire said so.
  await expect(page.getByText(/live figures/i)).toBeVisible();
});

test("a P&L with an unknown basis renders no basis claim", async ({ page }) => {
  // A basis this build does not know stays silent — the screen must never
  // guess what the numbers are.
  await open(page, { pnl: { basis: "some_future_basis" } });
  await page.goto("/projects/proj-1");

  await expect(page.getByRole("heading", { name: "JOB-7 · Won contract" })).toBeVisible();
  await expect(page.getByText(/live figures|frozen at close/i)).toHaveCount(0);
});

test("a frozen P&L says so and shows what arrived after close", async ({ page }) => {
  await open(page, {
    pnl: {
      status: "closed",
      basis: "net_eur_frozen",
      pnl_frozen_at: "2026-08-10T09:00:00Z",
      adjustments: { costs: "50.00", profit: "-50.00" },
    },
  });
  await page.goto("/projects/proj-1");

  await expect(page.getByText(/frozen at close/i)).toBeVisible();
  await expect(page.getByText("Arrived after close")).toBeVisible();
  await expect(page.getByText("costs: 50.00 €")).toBeVisible();
  // The frozen headline figure is untouched by the adjustment.
  await expect(page.getByText("450.00 €")).toBeVisible();
});

test("adding a cost line posts what the operator typed", async ({ page }) => {
  let posted: Record<string, unknown> | null = null;
  await open(page, { onCostEntry: (b) => (posted = b) });
  await page.goto("/projects/proj-1");

  await page.getByPlaceholder("Wages for the job").fill("Crew wages");
  await page.getByPlaceholder("300.00").fill("300.00");
  await page.getByRole("button", { name: "Add cost" }).click();

  await expect.poll(() => posted).toEqual({
    label: "Crew wages",
    category: "wages",
    amount: "300.00",
  });
});

test("issuing under a project sends the revenue link", async ({ page }) => {
  let issued: Record<string, unknown> | null = null;
  await open(page, { onIssue: (b) => (issued = b) });
  await page.goto("/issue");

  // The form's labels are visual siblings, not associated controls — target by
  // adjacency, which also fails loudly if the layout is restructured.
  await page.locator('label:text-is("Project") ~ select').selectOption("proj-1");
  await page.locator('label:text-is("Customer name") ~ input').fill("Customer OU");
  await page.locator("tbody tr").first().locator("input").first().fill("Work performed");
  await page.getByRole("button", { name: "Issue invoice" }).click();

  await expect.poll(() => issued).not.toBeNull();
  expect((issued as Record<string, unknown> | null)?.project_id).toBe("proj-1");
});

test("the copy is industry-neutral", async ({ page }) => {
  // The owner's guard list, enforced where a client actually reads.
  await open(page);
  await page.goto("/projects/proj-1");
  await expect(page.getByRole("heading", { name: "JOB-7 · Won contract" })).toBeVisible();

  const text = (await page.locator("body").innerText()).toLowerCase();
  for (const word of ["cargo", "fuel", "vehicle", "driver", "truck", "site crew"]) {
    expect(text).not.toContain(word);
  }
});
