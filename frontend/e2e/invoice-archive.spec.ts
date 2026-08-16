import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * The platform archive as the CLIENT sees it (docs/design/platform-archive.md),
 * over the LIVE app shell with the API mocked via `page.route` — the same
 * deterministic pattern as `invoice-trash.spec.ts`, no backend involved.
 *
 * What this proves, and why each item earns its place:
 *
 *  - **The route resolves.** `/invoices/archive` sits next to `/invoices/:id`;
 *    ordered wrongly, React Router reads "archive" as an invoice id and renders
 *    the detail page's not-found — which looks exactly like an empty archive.
 *    That is the same trap `/invoices/trash` fell into and the reason both are
 *    declared before the dynamic segment.
 *  - **The retention period comes from the server.** A screen that hardcoded
 *    three years would keep saying three years the day the archive keeps four,
 *    and the person it misleads is the client deciding whether to extend.
 *  - **A record inside the notice window is called out.** The whole point of the
 *    notice window is that expiry is never a surprise; a row that expires next
 *    week must not look identical to one that expires in two years.
 *  - **The archive is reachable from the bin**, which is where somebody looking
 *    for something they deleted actually goes.
 *  - **A role without ARCHIVE_READ gets a sentence, not a 403.** The server is
 *    the control; this is about what the person reads.
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

/** Far enough out that it is never inside a 60-day notice window. */
const FAR = new Date(Date.now() + 900 * 86_400_000).toISOString();
/** Inside any plausible notice window, without pinning a specific day count. */
const SOON = new Date(Date.now() + 10 * 86_400_000).toISOString();

function archived(overrides: Record<string, unknown> = {}) {
  return {
    id: "arc-1",
    original_invoice_id: "inv-1",
    invoice_number: "INV-2026-0041",
    vendor_name: "Fictional Fuels OU",
    issue_date: "2026-06-01",
    currency: "EUR",
    total: "1240.50",
    line_items: [{ description: "Diesel", quantity: "410", line_total: "1240.50" }],
    has_document: true,
    source_filename: "fictional-fuels-0041.pdf",
    original_deleted_at: "2026-07-01T09:00:00Z",
    original_deleted_by: "someone@test.io",
    archived_at: "2026-07-31T09:00:00Z",
    expires_at: FAR,
    ...overrides,
  };
}

interface MockOpts {
  items?: Record<string, unknown>[];
  role?: string;
  retentionYears?: number;
  noticeDays?: number;
  total?: number;
  /** Records the `offset` the SPA asked for. */
  onList?: (offset: string) => void;
  /** Records the archive id whose document the SPA asked for. */
  onDocument?: (archiveId: string) => void;
  /** Serve the document route as a 404 — the row outliving its bytes. */
  documentMissing?: boolean;
}

async function open(page: Page, opts: MockOpts = {}) {
  const items = opts.items ?? [archived()];

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

    const doc = url.pathname.match(/\/archive\/([^/]+)\/document$/);
    if (doc) {
      opts.onDocument?.(doc[1]);
      if (opts.documentMissing)
        return route.fulfill(json({ detail: "Stored document missing" }, 404));
      return route.fulfill({
        status: 200,
        contentType: "application/octet-stream",
        body: "%PDF-1.4 synthetic",
      });
    }

    if (path.startsWith("/archive")) {
      opts.onList?.(url.searchParams.get("offset") ?? "");
      return route.fulfill(
        json({
          items,
          total: opts.total ?? items.length,
          retention_years: opts.retentionYears ?? 3,
          expiry_notice_days: opts.noticeDays ?? 60,
        }),
      );
    }

    if (path.startsWith("/invoices/trash"))
      return route.fulfill(json({ items: [], total: 0, retention_days: 30 }));

    return route.fulfill(json({ items: [], total: 0 }));
  });

  await page.goto("/invoices/archive");
}

test("the archive route renders the archive, not the invoice-detail not-found", async ({
  page,
}) => {
  await open(page);

  await expect(page.getByRole("heading", { name: "Archive" })).toBeVisible();
  await expect(page.getByText("INV-2026-0041")).toBeVisible();
  await expect(page.getByText("Fictional Fuels OU")).toBeVisible();
});

test("the retention period shown is the one the server sent", async ({ page }) => {
  // Deliberately NOT 3: a page that hardcoded the real value would still pass
  // against 3 and drift silently the day the policy changes.
  await open(page, { retentionYears: 7 });

  await expect(page.getByText(/kept for 7 years/i)).toBeVisible();
});

test("the screen says plainly that an archived invoice cannot be restored", async ({
  page,
}) => {
  // There is no restore route and there should not be. Somebody who came here
  // from the bin has just used one, so the absence has to be stated rather than
  // left to be discovered.
  await open(page);

  await expect(page.getByText(/cannot be restored into your books/i)).toBeVisible();
});

test("a record close to expiry is called out; one far off is not", async ({ page }) => {
  await open(page, {
    items: [
      archived({ id: "arc-far", invoice_number: "INV-FAR", expires_at: FAR }),
      archived({ id: "arc-soon", invoice_number: "INV-SOON", expires_at: SOON }),
    ],
  });

  await expect(page.getByText(/leaves in 10 days/i)).toBeVisible();
  // Exactly one row is flagged — the warning means nothing if every row wears it.
  await expect(page.getByText(/leaves in \d+ days?/i)).toHaveCount(1);
});

test("the notice window is the server's, not the page's", async ({ page }) => {
  // With a 5-day window the same record is NOT yet inside it. A page holding its
  // own 60 would flag it and be wrong.
  await open(page, { noticeDays: 5, items: [archived({ expires_at: SOON })] });

  // Anchor on something PRESENT before asserting something absent. Without this
  // the negative assertion resolved against a page that had not finished its
  // first fetch — it passed with the row unrendered, and went on passing when
  // the notice window was deliberately hardcoded back to 60. A "proves nothing"
  // test is worse than no test: it is a green tick over an unchecked claim.
  await expect(page.getByText("INV-2026-0041")).toBeVisible();
  await expect(page.getByText(/leaves in/i)).toHaveCount(0);
});

test("downloading asks the server for that record's document", async ({ page }) => {
  let asked: string | null = null;
  await open(page, { onDocument: (id) => (asked = id) });

  await page.getByRole("button", { name: "Download" }).click();

  await expect.poll(() => asked).toBe("arc-1");
});

test("a record with no source document explains the disabled button", async ({ page }) => {
  await open(page, {
    items: [archived({ has_document: false, source_filename: null })],
  });

  const button = page.getByRole("button", { name: "Download" });
  await expect(button).toBeDisabled();
  await expect(button).toHaveAttribute("title", /entered by hand/i);
});

test("a row that outlived its bytes says so instead of failing silently", async ({
  page,
}) => {
  // The API returns 404 when the record survives but the stored file does not.
  // A button that appears to do nothing is the worst outcome on the one control
  // carrying the evidentiary value of the feature.
  await open(page, { documentMissing: true });

  await page.getByRole("button", { name: "Download" }).click();

  await expect(page.getByText(/Stored document missing/i)).toBeVisible();
});

test("expanding a record shows the line detail it was archived with", async ({ page }) => {
  await open(page);

  await page.getByRole("button", { name: "INV-2026-0041" }).click();

  await expect(page.getByText("Diesel")).toBeVisible();
  await expect(page.getByText("inv-1")).toBeVisible();
});

test("an empty archive states absence positively and says why", async ({ page }) => {
  await open(page, { items: [] });

  await expect(page.getByText("Nothing archived yet")).toBeVisible();
  await expect(page.getByRole("table")).toHaveCount(0);
});

test("a role without archive access reads a sentence rather than an error", async ({
  page,
}) => {
  // Cosmetic only — the server refuses the router outright. This is about the
  // person understanding who to ask.
  await open(page, { role: "finance_manager" });

  await expect(page.getByText(/readable only by an administrator or the company owner/i)).toBeVisible();
  await expect(page.getByRole("table")).toHaveCount(0);
});

test("the archive is reachable from the recycle bin", async ({ page }) => {
  await open(page);
  await page.goto("/invoices/trash");

  await page.getByRole("link", { name: "Archive", exact: true }).click();

  await expect(page.getByRole("heading", { name: "Archive" })).toBeVisible();
});

test("the archive does not claim more rows than it can show", async ({ page }) => {
  const rows = Array.from({ length: 50 }, (_, i) =>
    archived({ id: `arc-${i}`, invoice_number: `INV-${i}` }),
  );
  await open(page, { items: rows, total: 120 });

  await expect(page.getByText("Showing 50 of 120 archived records.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Next" })).toBeEnabled();
});

test("the pager asks the server for the next page", async ({ page }) => {
  const offsets: string[] = [];
  const rows = Array.from({ length: 50 }, (_, i) =>
    archived({ id: `arc-${i}`, invoice_number: `INV-${i}` }),
  );
  await open(page, { items: rows, total: 120, onList: (o) => offsets.push(o) });

  await page.getByRole("button", { name: "Next" }).click();

  await expect.poll(() => offsets).toContain("50");
});
