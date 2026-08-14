import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * The failed-capture worklist (H-1) and its bulk multi-select (L-4), over the
 * LIVE app shell with the API mocked via `page.route` — the deterministic
 * pattern used by the other suites, no backend involved.
 *
 * What this proves, and why each item is here:
 *
 *  - **The client sends a correct `agreed_count`.** That value IS guard 1: the
 *    server refuses the batch when it disagrees with what arrived, so a client
 *    that computes it wrongly silently disables the guard while every test on
 *    the server side still passes. Nothing else checks this.
 *  - **Skips render as information, not as errors.** "Already acknowledged" is
 *    the system working; painting it red is how operators learn to ignore red.
 *  - **Only unresolved rows are selectable** — offering a checkbox on a row that
 *    cannot be acted on produces a selection the server will only skip.
 *  - The page states absence positively when nothing has failed, rather than
 *    rendering an empty table that reads as "all clear".
 *
 * Synthetic fixtures only: fictional filenames and ids.
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

function failure(overrides: Record<string, unknown> = {}) {
  return {
    channel: "upload",
    ref_id: "run-1",
    code: "malformed_document",
    summary: "The file is a supported type, but no invoice could be read out of it.",
    remediation: "Open the file to check it is the invoice itself and is not damaged.",
    retry_helps: false,
    user_fixable: true,
    detail: "CSV needs at least one of: amount, description",
    source_filename: "statement.csv",
    sha256: "a".repeat(64),
    document_retained: true,
    failed_at: "2026-08-14T09:00:00Z",
    repeat_count: 1,
    acknowledged_at: null,
    acknowledged_by: null,
    acknowledgement_note: null,
    ...overrides,
  };
}

interface MockOpts {
  items?: Record<string, unknown>[];
  /** Captures the body the SPA posted to the bulk endpoint. */
  onBulk?: (body: Record<string, unknown>) => void;
  bulkResponse?: Record<string, unknown>;
}

async function mockApi(page: Page, opts: MockOpts = {}) {
  const items = opts.items ?? [failure()];

  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));

  const json = (body: unknown, code = 200) => ({
    status: code,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

  const worklist = (rows: Record<string, unknown>[]) => ({
    items: rows,
    groups: [],
    total: rows.length,
    unacknowledged: rows.filter((r) => !r.acknowledged_at).length,
  });

  await page.route("**/api/v1/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^.*\/api\/v1/, "") + url.search;

    if (path === "/auth/me") return route.fulfill(json({ user: USER, organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/modules") return route.fulfill(json([]));

    if (path.startsWith("/invoices/captures/failures/acknowledge")) {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      opts.onBulk?.(body);
      return route.fulfill(
        json(
          opts.bulkResponse ?? {
            applied: 1,
            skipped: 0,
            failed: 0,
            outcomes: [{ ref_id: "run-1", result: "applied", reason: null }],
            applied_ids: ["run-1"],
            worklist: worklist([]),
          },
        ),
      );
    }

    if (path.startsWith("/invoices/captures/failures")) return route.fulfill(json(worklist(items)));

    return route.fulfill(json({}));
  });
}

async function open(page: Page, opts: MockOpts = {}) {
  await mockApi(page, opts);
  await page.goto("/captures/failures");
}

test("a failed capture renders its remediation, not just that it failed", async ({ page }) => {
  await open(page);

  // Exact: the checkbox carries a screen-reader label ("Select statement.csv")
  // that a loose match would also hit.
  await expect(page.getByText("statement.csv", { exact: true })).toBeVisible();
  await expect(
    page.getByText("The file is a supported type, but no invoice could be read out of it."),
  ).toBeVisible();
  await expect(
    page.getByText("Open the file to check it is the invoice itself and is not damaged."),
  ).toBeVisible();
  // The "what survived" half of the record.
  await expect(page.getByText(/original document is still stored/i)).toBeVisible();
});

test("the client posts an agreed_count matching the selection it displayed", async ({ page }) => {
  // GUARD 1 lives half on the client. If this number is wrong, the server's
  // check passes vacuously and the guard is silently off.
  let posted: Record<string, unknown> | null = null;
  await open(page, {
    items: [failure(), failure({ ref_id: "run-2", source_filename: "second.csv" })],
    onBulk: (body) => {
      posted = body;
    },
  });

  await page.getByRole("button", { name: "Select all unresolved" }).click();
  await page.getByRole("button", { name: /Acknowledge 2/ }).click();

  await expect.poll(() => posted).not.toBeNull();
  const body = posted as unknown as { agreed_count: number; items: unknown[]; selection: string };
  expect(body.agreed_count).toBe(2);
  expect(body.items).toHaveLength(2);
  expect(body.agreed_count).toBe(body.items.length);
  // An explicit, enumerated selection — the only mode allowed to drive something
  // irreversible later.
  expect(body.selection).toBe("explicit");
});

test("selecting one row posts exactly that row", async ({ page }) => {
  let posted: Record<string, unknown> | null = null;
  await open(page, {
    items: [failure(), failure({ ref_id: "run-2", source_filename: "second.csv" })],
    onBulk: (body) => {
      posted = body;
    },
  });

  await page.locator("#sel-run-2").check();
  await page.getByRole("button", { name: /Acknowledge 1/ }).click();

  await expect.poll(() => posted).not.toBeNull();
  const body = posted as unknown as {
    agreed_count: number;
    items: { ref_id: string; channel: string }[];
  };
  expect(body.agreed_count).toBe(1);
  expect(body.items).toEqual([{ channel: "upload", ref_id: "run-2" }]);
});

test("an already-acknowledged row offers no checkbox", async ({ page }) => {
  await open(page, {
    items: [
      failure({
        ref_id: "run-ack",
        source_filename: "done.csv",
        acknowledged_at: "2026-08-14T10:00:00Z",
        acknowledged_by: "someone@test.io",
        acknowledgement_note: "handled",
      }),
    ],
  });

  await expect(page.getByText("done.csv")).toBeVisible();
  await expect(page.locator("#sel-run-ack")).toHaveCount(0);
});

test("a skipped record is reported as information, never as an error", async ({ page }) => {
  await open(page, {
    items: [failure(), failure({ ref_id: "run-2", source_filename: "second.csv" })],
    bulkResponse: {
      applied: 1,
      skipped: 1,
      failed: 0,
      outcomes: [
        { ref_id: "run-1", result: "applied", reason: null },
        {
          ref_id: "run-2",
          result: "skipped",
          reason: "Already acknowledged since this failure.",
        },
      ],
      applied_ids: ["run-1"],
      worklist: { items: [], groups: [], total: 0, unacknowledged: 0 },
    },
  });

  await page.getByRole("button", { name: "Select all unresolved" }).click();
  await page.getByRole("button", { name: /Acknowledge 2/ }).click();

  await expect(page.getByText("Acknowledged 1 of 2.")).toBeVisible();
  const skip = page.getByText("Already acknowledged since this failure.");
  await expect(skip).toBeVisible();
  // The reason must not be styled as an error. Asserted on the rendered colour
  // rather than a class name, so a restyle that turns skips red still fails.
  const colour = await skip.evaluate((el) => getComputedStyle(el.closest("p")!).color);
  expect(colour).not.toMatch(/rgb\(2[0-9]{2}, [0-9]{1,2}, [0-9]{1,2}\)/);
});

test("select-all never builds a batch the server would refuse", async ({ page }) => {
  // The worklist is UNPAGINATED, and the server caps a batch at 200
  // (`bulk_too_many`). Without a client cap, "Select all unresolved" on a large
  // tenant builds a selection that can only ever be rejected — a button that is
  // guaranteed to fail is worse than no button.
  let posted: Record<string, unknown> | null = null;
  const many = Array.from({ length: 250 }, (_, i) =>
    failure({ ref_id: `run-${i}`, source_filename: `f${i}.csv` }),
  );
  await open(page, {
    items: many,
    onBulk: (body) => {
      posted = body;
    },
  });

  await page.getByRole("button", { name: /Select all unresolved/ }).click();
  await page.getByRole("button", { name: /Acknowledge 200/ }).click();

  await expect.poll(() => posted).not.toBeNull();
  const body = posted as unknown as { agreed_count: number; items: unknown[] };
  expect(body.items).toHaveLength(200);
  expect(body.agreed_count).toBe(200);
});

test("nothing failed is stated positively, not as an empty table", async ({ page }) => {
  await open(page, { items: [] });

  await expect(page.getByText(/Every document we received has been read/i)).toBeVisible();
});
