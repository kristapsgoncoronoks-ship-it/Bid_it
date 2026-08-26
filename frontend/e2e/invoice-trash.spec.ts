import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * The recycle bin (docs/design/deletion-and-archive.md, step 2), over the LIVE
 * app shell with the API mocked via `page.route` — the deterministic pattern the
 * other suites use, no backend involved.
 *
 * What this proves, and why each item earns its place:
 *
 *  - **The route resolves.** `/invoices/trash` sits next to `/invoices/:id`;
 *    ordered wrongly, React Router reads "trash" as an invoice id and renders
 *    the detail page's not-found. That failure looks exactly like an empty bin,
 *    which is the most misleading thing this screen could possibly do.
 *  - **The retention window comes from the server.** If the page ever hardcodes
 *    30, the number on screen and the number the purge enforces can drift, and
 *    the client is the one who finds out.
 *  - **Restore stays visible but disabled for a role that cannot use it.** A
 *    finance manager who deleted the invoice needs to know it is recoverable and
 *    who to ask; an absent button says "gone".
 *  - **The bin is reachable from the invoice list**, which is where somebody who
 *    just deleted something actually looks.
 *
 * Synthetic fixtures only: fictional suppliers and ids.
 */

const ORG = { id: "org-1", name: "Test Workspace", status: "active" };

function user(role: string) {
  return {
    id: "user-1",
    email: "someone@test.io",
    name: "Test User",
    role,
    org_id: "org-1",
    is_platform_admin: false,
  };
}

function binned(overrides: Record<string, unknown> = {}) {
  return {
    invoice_id: "inv-1",
    invoice_number: "INV-2026-0041",
    vendor_name: "Fictional Fuels OU",
    issue_date: "2026-06-01",
    currency: "EUR",
    total: "1240.50",
    deleted_at: "2026-08-14T09:00:00Z",
    deleted_by: "someone@test.io",
    days_left: 28,
    ...overrides,
  };
}

interface MockOpts {
  items?: Record<string, unknown>[];
  role?: string;
  retentionDays?: number;
  /** Overrides the count the server reports, so the truncated case is testable. */
  total?: number;
  /** Records the `offset` the SPA asked for. */
  onList?: (offset: string) => void;
  /** Records the invoice id the SPA asked to restore. */
  onRestore?: (invoiceId: string) => void;
}

async function open(page: Page, opts: MockOpts = {}) {
  const items = opts.items ?? [binned()];

  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));

  const json = (body: unknown, code = 200) => ({
    status: code,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

  await page.route("**/api/v1/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^.*\/api\/v1/, "") + url.search;

    if (path === "/auth/me")
      return route.fulfill(json({ user: user(opts.role ?? "owner"), organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/modules") return route.fulfill(json([]));

    const otherRestore = path.match(/^\/invoices\/trash\/other\/([^/]+)\/([^/]+)\/restore/);
    if (otherRestore) {
      opts.onOtherRestore?.(`${otherRestore[1]}:${otherRestore[2]}`);
      return route.fulfill(json({ kind: otherRestore[1], id: otherRestore[2], summary: {} }));
    }
    if (path.startsWith("/invoices/trash/other")) {
      return route.fulfill(
        json({
          retention_days: 30,
          items: [
            {
              kind: "expense_report",
              label: "Expense report",
              id: "rep-1",
              summary: { title: "Overlap trip", employee: "Test User" },
              deleted_at: "2026-08-20T10:00:00+00:00",
              deleted_by: "owner@test.io",
              days_left: 24,
            },
            {
              kind: "recurring_schedule",
              label: "Recurring schedule",
              id: "rec-1",
              summary: { title: "Monthly retainer", frequency: "monthly" },
              deleted_at: "2026-08-21T10:00:00+00:00",
              deleted_by: null,
              days_left: 25,
            },
          ],
        }),
      );
    }

    const restore = path.match(/^\/invoices\/([^/]+)\/restore/);
    if (restore) {
      opts.onRestore?.(restore[1]);
      return route.fulfill(json({ id: restore[1] }));
    }

    if (path.startsWith("/invoices/trash")) {
      opts.onList?.(url.searchParams.get("offset") ?? "");
      return route.fulfill(
        json({
          items,
          total: opts.total ?? items.length,
          retention_days: opts.retentionDays ?? 30,
        }),
      );
    }

    return route.fulfill(json({ items: [], total: 0 }));
  });

  await page.goto("/invoices/trash");
}

test("the trash route renders the bin, not the invoice-detail not-found", async ({ page }) => {
  await open(page);

  await expect(page.getByRole("heading", { name: "Deleted invoices" })).toBeVisible();
  await expect(page.getByText("INV-2026-0041")).toBeVisible();
  await expect(page.getByText("Fictional Fuels OU")).toBeVisible();
});

test("the retention window shown is the one the server sent", async ({ page }) => {
  // Deliberately NOT 30: a page that hardcoded the real value would still pass
  // against 30 and drift silently the day the policy changes.
  await open(page, { retentionDays: 45 });

  await expect(page.getByText(/restored for 45 days/i)).toBeVisible();
});

test("a record whose window has run out says so rather than showing zero days", async ({
  page,
}) => {
  await open(page, { items: [binned({ days_left: 0 })] });

  await expect(page.getByText("Due to be removed")).toBeVisible();
});

test("restore posts for the invoice the operator chose", async ({ page }) => {
  let restored: string | null = null;
  await open(page, { onRestore: (id) => (restored = id) });

  await page.getByRole("button", { name: "Restore" }).click();

  await expect.poll(() => restored).toBe("inv-1");
});

test("a role that cannot restore still sees the button, disabled and explained", async ({
  page,
}) => {
  // The finance manager can delete but not restore. Hiding the control would
  // read as "this cannot be recovered"; disabling it with the reason says who
  // to ask.
  await open(page, { role: "finance_manager" });

  const button = page.getByRole("button", { name: "Restore" });
  await expect(button).toBeVisible();
  await expect(button).toBeDisabled();
  await expect(button).toHaveAttribute("title", /administrator or the company owner/i);
});

test("an empty bin states absence positively instead of showing a bare table", async ({
  page,
}) => {
  await open(page, { items: [] });

  await expect(page.getByText("Nothing deleted")).toBeVisible();
  await expect(page.getByRole("table")).toHaveCount(0);
});

test("the bin is reachable from the invoice list", async ({ page }) => {
  await open(page);
  await page.goto("/invoices");

  await page.getByRole("link", { name: "Deleted", exact: true }).click();

  await expect(page.getByRole("heading", { name: "Deleted invoices" })).toBeVisible();
});

test("the bin does not claim more rows than it can show", async ({ page }) => {
  // The defect this replaced: the page asked for no page size, took the server's
  // default 50, and printed the UNPAGINATED total above them. After one bulk
  // delete it read "120 invoices deleted" over 50 rows with no way to reach the
  // rest — the screen stating something untrue about deleted records.
  const rows = Array.from({ length: 50 }, (_, i) =>
    binned({ invoice_id: `inv-${i}`, invoice_number: `INV-${i}` }),
  );
  await open(page, { items: rows, total: 120 });

  await expect(page.getByText("Showing 50 of 120 deleted invoices.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Next" })).toBeEnabled();
});

test("the pager asks the server for the next page", async ({ page }) => {
  const offsets: string[] = [];
  const rows = Array.from({ length: 50 }, (_, i) =>
    binned({ invoice_id: `inv-${i}`, invoice_number: `INV-${i}` }),
  );
  await open(page, { items: rows, total: 120, onList: (o) => offsets.push(o) });

  await page.getByRole("button", { name: "Next" }).click();

  await expect.poll(() => offsets).toContain("50");
});


test("WO-M: the generic bin lists other entities and restores by kind", async ({ page }) => {
  const restored: string[] = [];
  await open(page, { onOtherRestore: (r) => restored.push(r) });

  await expect(page.getByRole("heading", { name: "Other deleted items" })).toBeVisible();
  await expect(page.getByText("Overlap trip", { exact: false })).toBeVisible();
  await expect(page.getByText("Monthly retainer", { exact: false })).toBeVisible();

  const row = page.locator("tr", { hasText: "Overlap trip" });
  await row.getByRole("button", { name: "Restore" }).click();
  await expect.poll(() => restored.length).toBe(1);
  expect(restored[0]).toBe("expense_report:rep-1");
});
