import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * Multi-select delete on the invoice list
 * (docs/design/deletion-and-archive.md, step 5) — the LIVE app shell with the
 * API mocked via `page.route`.
 *
 * This step was held back until deletion was reversible, and that is the only
 * reason it is acceptable at all: everything it does lands in the recycle bin
 * for 30 days.
 *
 * What only a client test can prove:
 *
 *  - **`agreed_count` matches what the screen displayed.** That value IS guard 1.
 *    A client that computes it wrongly silently disables the guard while every
 *    server-side test still passes. Nothing else checks this.
 *  - **`selection` is always `explicit`.** Guard 4 refuses a filter-based batch;
 *    a client that sent "filter" would be refused, but one that sent nothing
 *    would be relying on a server default.
 *  - **Only a draft is selectable.** A checkbox on a row the server will only
 *    skip teaches the operator to distrust every control.
 *  - **Skips are shown as information, with their reasons.** Collapsing a batch
 *    to "done" throws away the half the operator most needs.
 *
 * Synthetic fixtures only.
 */

const ORG = { id: "org-1", name: "Test Workspace", status: "active" };
const USER = {
  id: "user-1",
  email: "owner@test.io",
  name: "Test User",
  role: "owner",
  org_id: "org-1",
  is_platform_admin: false,
};

function invoice(id: string, number: string, workflow_state = "draft") {
  return {
    id,
    vendor_id: "v-1",
    invoice_number: number,
    issue_date: "2026-06-01",
    due_date: null,
    currency: "EUR",
    status: "pending",
    subtotal: "100.00",
    tax_amount: "0.00",
    total: "100.00",
    validation_status: "none",
    workflow_state,
    source_filename: null,
  };
}

interface MockOpts {
  items?: ReturnType<typeof invoice>[];
  onBulk?: (body: Record<string, unknown>) => void;
  bulkResponse?: Record<string, unknown>;
}

async function open(page: Page, opts: MockOpts = {}) {
  const items = opts.items ?? [invoice("inv-1", "INV-A"), invoice("inv-2", "INV-B")];

  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));

  const json = (body: unknown, code = 200) => ({
    status: code,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

  await page.route("**/api/v1/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^.*\/api\/v1/, "");

    if (path === "/auth/me") return route.fulfill(json({ user: USER, organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/modules") return route.fulfill(json([]));

    if (path === "/invoices/bulk-delete") {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      opts.onBulk?.(body);
      return route.fulfill(
        json(
          opts.bulkResponse ?? {
            deleted: (body.invoice_ids as string[]).length,
            skipped: 0,
            failed: 0,
            outcomes: (body.invoice_ids as string[]).map((id) => ({
              ref_id: id,
              result: "applied",
              reason: null,
            })),
            deleted_records: [],
          },
        ),
      );
    }

    if (path === "/invoices") return route.fulfill(json({ items, total: items.length }));

    return route.fulfill(json({ items: [], total: 0 }));
  });

  await page.goto("/invoices");
}

test("the client posts an agreed_count matching the selection it displayed", async ({ page }) => {
  // GUARD 1 lives half here. If this number is wrong, the server's check passes
  // vacuously and the guard is silently off.
  let posted: Record<string, unknown> | null = null;
  await open(page, { onBulk: (b) => (posted = b) });

  await page.getByRole("checkbox", { name: "Select INV-A" }).check();
  await page.getByRole("checkbox", { name: "Select INV-B" }).check();
  await expect(page.getByText("2 selected")).toBeVisible();
  await page.getByRole("button", { name: "Delete", exact: true }).click();

  await expect.poll(() => posted).not.toBeNull();
  expect(posted!.agreed_count).toBe(2);
  expect((posted!.invoice_ids as string[]).sort()).toEqual(["inv-1", "inv-2"]);
  // GUARD 4: never a filter-based selection, and never left to a server default.
  expect(posted!.selection).toBe("explicit");
});

test("a non-draft row is not selectable at all", async ({ page }) => {
  await open(page, {
    items: [invoice("inv-1", "INV-DRAFT"), invoice("inv-2", "INV-APPROVED", "approved")],
  });

  await expect(page.getByRole("checkbox", { name: "Select INV-DRAFT" })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: "Select INV-APPROVED" })).toHaveCount(0);
});

test("select-all takes only the drafts on the page", async ({ page }) => {
  let posted: Record<string, unknown> | null = null;
  await open(page, {
    items: [
      invoice("inv-1", "INV-D1"),
      invoice("inv-2", "INV-PAID", "paid"),
      invoice("inv-3", "INV-D2"),
    ],
    onBulk: (b) => (posted = b),
  });

  await page.getByRole("checkbox", { name: "Select every draft on this page" }).check();
  await expect(page.getByText("2 selected")).toBeVisible();
  await page.getByRole("button", { name: "Delete", exact: true }).click();

  await expect.poll(() => posted).not.toBeNull();
  expect((posted!.invoice_ids as string[]).sort()).toEqual(["inv-1", "inv-3"]);
});

test("skips are reported with their reasons, not swallowed", async ({ page }) => {
  await open(page, {
    bulkResponse: {
      deleted: 1,
      skipped: 1,
      failed: 0,
      outcomes: [
        { ref_id: "inv-1", result: "applied", reason: null },
        { ref_id: "inv-2", result: "skipped", reason: "A payment is recorded against it." },
      ],
      deleted_records: [],
    },
  });

  await page.getByRole("checkbox", { name: "Select INV-A" }).check();
  await page.getByRole("button", { name: "Delete", exact: true }).click();

  await expect(page.getByText(/Moved 1 to deleted items, left 1 alone/)).toBeVisible();
  await expect(page.getByText("A payment is recorded against it.")).toBeVisible();
});

test("the result points at the bin, because the action is undoable", async ({ page }) => {
  // The reason this whole step was safe to build. An operator who has just
  // deleted twenty invoices needs the way back in front of them, not in a
  // sidebar they have to go looking for.
  await open(page);

  await page.getByRole("checkbox", { name: "Select INV-A" }).check();
  await page.getByRole("button", { name: "Delete", exact: true }).click();

  const link = page.getByRole("link", { name: /Restore within 30 days/ });
  await expect(link).toBeVisible();
  await expect(link).toHaveAttribute("href", "/invoices/trash");
});
