import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * PROD-009 — "export everything" on the admin panel.
 *
 * The audit found a customer could put their whole business into this platform
 * and had no way to take it out. The server side is proven in
 * `tests/test_prod009_workspace_export.py`; what earns a place HERE is what
 * only the page can get wrong, in order of how quietly each would fail:
 *
 *  - the card says what is in the file AND what is deliberately not, because a
 *    person deciding whether to trust an export asks both;
 *  - asking POSTs, and the confirmation names the address the one-time link
 *    goes to — the link never crosses this wire, so the page must say where to
 *    look for it;
 *  - the history reads back with counts, and an export whose bytes have been
 *    destroyed says so rather than offering a dead link;
 *  - a failed build shows its reason instead of sitting on "building" for ever;
 *  - a failed READ of the history shows an alert, not an empty list that reads
 *    as "you have never exported" (FE-004);
 *  - a non-owner cannot use the button and is told why, mirroring the server.
 *
 * Live app shell, API mocked via page.route. Synthetic fixtures only.
 */

const ORG = { id: "org-1", name: "Northwind Components", status: "active" };

const json = (body: unknown, code = 200) => ({
  status: code,
  contentType: "application/json",
  body: JSON.stringify(body),
});

interface Opts {
  role?: string;
  requests?: unknown[];
  requestsFail?: boolean;
  onPost?: () => void;
}

const READY = {
  id: "wx-1",
  status: "downloaded",
  requested_email: "owner@northwind-components.example",
  created_at: "2026-09-08T09:00:00Z",
  ready_at: "2026-09-08T09:02:00Z",
  link_expires_at: "2026-09-15T09:02:00Z",
  downloaded_at: "2026-09-08T10:00:00Z",
  purged_at: null,
  rows: 48213,
  tables: 105,
  documents: 317,
  missing_documents: 0,
  size: 91234567,
  error: null,
};

const PURGED = { ...READY, id: "wx-0", purged_at: "2026-08-01T00:00:00Z", created_at: "2026-07-24T09:00:00Z" };

const FAILED = {
  ...READY,
  id: "wx-2",
  status: "failed",
  rows: null,
  tables: null,
  documents: null,
  purged_at: null,
  error: "the export is 812343222 bytes, above the 536870912-byte ceiling this deployment stores in one piece",
};

async function openSettings(page: Page, opts: Opts = {}) {
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));
  await page.route("**/api/v1/**", async (route: Route) => {
    const p = new URL(route.request().url()).pathname.replace(/^.*\/api\/v1/, "");
    const method = route.request().method();

    if (p === "/auth/me")
      return route.fulfill(
        json({
          user: {
            id: "user-1",
            email: "owner@northwind-components.example",
            name: "Owner",
            role: opts.role ?? "owner",
            org_id: "org-1",
            is_platform_admin: false,
          },
          organization: ORG,
        }),
      );
    if (p === "/auth/organizations") return route.fulfill(json([ORG]));
    if (p === "/modules") return route.fulfill(json([]));

    if (p === "/workspace/export-requests") {
      if (opts.requestsFail) return route.fulfill(json({ detail: "boom" }, 500));
      return route.fulfill(json(opts.requests ?? []));
    }
    if (p === "/workspace/export" && method === "POST") {
      opts.onPost?.();
      return route.fulfill(
        json(
          {
            ...READY,
            id: "wx-new",
            status: "queued",
            ready_at: null,
            downloaded_at: null,
            rows: null,
            tables: null,
            documents: null,
          },
          202,
        ),
      );
    }

    // The rest of the admin panel: array-shaped feeds must be arrays or a
    // sibling card crashes the page before this one renders.
    if (p === "/settings/schedule")
      return route.fulfill(json({ assignment_remind_hours: null, client_notice_hours: null }));
    if (p === "/settings/lifecycle")
      return route.fulfill(json({ offer_prefix: null, final_invoice_requires_acceptance: false }));
    if (p === "/jobs" || p === "/webhooks") return route.fulfill(json([]));
    if (p === "/settings/validation")
      return route.fulfill(json({ ai_validation_enabled: false, human_validation_enabled: false }));
    if (p === "/retention") return route.fulfill(json({ categories: [], holds: [] }));
    if (p === "/sso/connection") return route.fulfill(json(null));
    if (p === "/sso/assignable-roles") return route.fulfill(json([]));

    return route.fulfill(json({ items: [], total: 0 }));
  });
  await page.goto("/settings");
}

test("the card says what is in the export and what is left out", async ({ page }) => {
  await openSettings(page);
  const card = page.locator("section").filter({ has: page.getByRole("heading", { name: "Export everything" }) });
  await expect(card).toContainText(/every record this workspace holds/i);
  await expect(card).toContainText(/recycle bin/i);
  // The honest half people do not think to ask about.
  await expect(page.getByText(/Passwords, API secrets and feed tokens are left out/i)).toBeVisible();
  // The third thing people ask, which the first version of this card left to
  // the email: how long the link lives.
  await expect(card).toContainText(/works once/i);
  await expect(card).toContainText(/expires after 7 days/i);
});

test("asking posts, and the confirmation names where the link goes", async ({ page }) => {
  let posted = 0;
  await openSettings(page, { onPost: () => (posted += 1) });
  await page.getByRole("button", { name: "Request an export" }).click();
  await expect(page.getByText(/owner@northwind-components\.example/)).toBeVisible();
  expect(posted).toBe(1);
  // The link itself must never appear on this screen.
  await expect(page.getByText("/workspace/export/download/")).toHaveCount(0);
});

test("the history reads back with its counts", async ({ page }) => {
  await openSettings(page, { requests: [READY] });
  const card = page.locator("section").filter({ has: page.getByRole("heading", { name: "Export everything" }) });
  await expect(card).toContainText("48,213");
  await expect(card).toContainText("105 tables");
  await expect(card).toContainText("downloaded");
});

test("an export whose file was destroyed says so, and stops calling itself ready", async ({ page }) => {
  // Purging leaves `status` as it was, so an export nobody downloaded is still
  // "ready" on the row. The badge must not say that beside "the file was deleted".
  await openSettings(page, { requests: [{ ...PURGED, status: "ready" }] });
  const card = page.locator("section").filter({ has: page.getByRole("heading", { name: "Export everything" }) });
  await expect(page.getByText(/The file was deleted on .*after its link expired/i)).toBeVisible();
  await expect(card).toContainText("expired");
  await expect(card).not.toContainText("ready");
});

test("a failed build shows its reason, not an endless 'building'", async ({ page }) => {
  await openSettings(page, { requests: [FAILED] });
  const card = page.locator("section").filter({ has: page.getByRole("heading", { name: "Export everything" }) });
  // A sentence the owner can act on comes first; the raw service reason is
  // kept beneath it, for the support conversation.
  await expect(card).toContainText(/Ask support to raise this workspace's export size limit/i);
  await expect(card).toContainText(/Reason recorded:/i);
  await expect(card).not.toContainText(/Building — the link is emailed/i);
});

test("a failed read of the history shows an alert, never an empty history", async ({ page }) => {
  await openSettings(page, { requestsFail: true });
  await expect(page.getByRole("alert").filter({ hasText: /export history/i })).toBeVisible();
  await expect(page.getByText("You have not asked for an export yet.")).toHaveCount(0);
});

test("a non-owner cannot use the button and is told why", async ({ page }) => {
  await openSettings(page, { role: "admin" });
  await expect(page.getByRole("button", { name: "Request an export" })).toBeDisabled();
  await expect(page.getByText(/only the workspace owner can ask for it/i)).toBeVisible();
});
