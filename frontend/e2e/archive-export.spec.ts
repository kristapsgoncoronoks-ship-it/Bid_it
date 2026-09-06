import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * WO-AI — the ex-client archive export (owner decision 2026-08-16 §1.C).
 *
 *  - A live OWNER asks from the Archive screen; the page says a one-time link
 *    goes by email — it never shows a link, because the API never returns one.
 *  - Anyone who is not the owner is not offered the button (the server refuses
 *    them; a dead button is worse than none).
 *  - An ex-client's owner, who cannot sign in, asks from the PUBLIC page by
 *    email; the page answers the same way whatever the address (no enumeration).
 *
 * Live app shell, API mocked via page.route. Synthetic fixtures only.
 */

const ORG = { id: "org-1", name: "Haulage Co", status: "active" };

const json = (body: unknown, code = 200) => ({
  status: code,
  contentType: "application/json",
  body: JSON.stringify(body),
});

const ARCHIVE = {
  items: [
    {
      id: "arc-1",
      original_invoice_id: "inv-old-1",
      invoice_number: "SUP-2025-0001",
      vendor_name: "Fictional Fuels OU",
      issue_date: "2025-03-01",
      currency: "EUR",
      total: "1210.00",
      line_items: [],
      has_document: true,
      source_filename: "depot-march.pdf",
      original_deleted_at: "2025-06-01T10:00:00Z",
      original_deleted_by: "owner@haulage.example",
      archived_at: "2025-07-01T10:00:00Z",
      expires_at: new Date(Date.now() + 700 * 86_400_000).toISOString(),
    },
  ],
  total: 1,
  retention_years: 3,
  expiry_notice_days: 90,
  longest_plan_retention_years: 3,
};

type Handler = (p: string, route: Route) => Promise<void> | void | false;

async function signedIn(page: Page, role: string, handler: Handler) {
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));
  const user = { id: "u1", email: "owner@haulage.example", name: "Owner", role, org_id: "org-1", is_platform_admin: false };
  await page.route("**/api/v1/**", async (route: Route) => {
    const p = new URL(route.request().url()).pathname.replace(/^.*\/api\/v1/, "");
    if (p === "/auth/me") return route.fulfill(json({ user, organization: ORG }));
    if (p === "/auth/organizations") return route.fulfill(json([{ org_id: "org-1", name: ORG.name, role, current: true }]));
    if (p === "/modules") return route.fulfill(json([]));
    if (p === "/dashboard/onboarding") return route.fulfill(json({ steps: [], done_count: 0, complete: true, dismissed: true, can_dismiss: true }));
    if (p === "/archive") return route.fulfill(json(ARCHIVE));
    const handled = await handler(p, route);
    if (handled === false) return route.fulfill(json({}));
  });
}

test("WO-AI: the owner asks for the whole archive and is told the link goes by email", async ({ page }) => {
  const posts: string[] = [];
  await signedIn(page, "owner", async (p, route) => {
    if (p === "/archive/export" && route.request().method() === "POST") {
      posts.push(p);
      return route.fulfill(
        json(
          {
            id: "exp-1",
            status: "queued",
            requested_email: "owner@haulage.example",
            created_at: "2026-09-06T08:00:00Z",
            ready_at: null,
            link_expires_at: null,
            downloaded_at: null,
            records: null,
            missing_documents: null,
            size: null,
          },
          202,
        ),
      );
    }
    return false;
  });
  await page.goto("/invoices/archive");
  await expect(page.getByText("SUP-2025-0001")).toBeVisible();
  await page.getByRole("button", { name: "Export the whole archive" }).click();
  await expect(page.getByRole("status")).toContainText("emailed to owner@haulage.example");
  await expect(page.getByRole("status")).toContainText("valid for 7 days");
  expect(posts).toEqual(["/archive/export"]);
  // No link on the page — there is none to show.
  await expect(page.getByRole("link", { name: /download/i })).toHaveCount(0);
});

test("WO-AI: an administrator can read the archive but is not offered the export", async ({ page }) => {
  await signedIn(page, "admin", () => false);
  await page.goto("/invoices/archive");
  await expect(page.getByText("SUP-2025-0001")).toBeVisible();
  await expect(page.getByRole("button", { name: "Export the whole archive" })).toHaveCount(0);
});

test("WO-AI: an ex-client's owner asks from the public page, which never says whether the address is known", async ({
  page,
}) => {
  const bodies: unknown[] = [];
  await page.route("**/api/v1/**", async (route: Route) => {
    const p = new URL(route.request().url()).pathname.replace(/^.*\/api\/v1/, "");
    if (p === "/archive/export/request" && route.request().method() === "POST") {
      bodies.push(route.request().postDataJSON());
      return route.fulfill(json({ sent: true }));
    }
    return route.fulfill(json({ detail: "Could not validate credentials" }, 401));
  });
  await page.goto("/archive-export");
  await expect(page.getByRole("heading", { name: "Request your archive" })).toBeVisible();
  await page.getByLabel("Owner's email").fill("former-owner@haulage.example");
  await page.getByRole("button", { name: "Email me a download link" }).click();
  await expect(page.getByRole("heading", { name: "Check your inbox" })).toBeVisible();
  await expect(page.getByText("works once and expires in 7 days")).toBeVisible();
  expect(bodies).toEqual([{ email: "former-owner@haulage.example" }]);
  // A public page: the 401 the mock answers everything else with must not bounce it to /login.
  await expect(page).toHaveURL(/\/archive-export$/);
});

test("WO-AI: the sign-in screen points a departed client at the public page", async ({ page }) => {
  await page.route("**/api/v1/**", (route: Route) => route.fulfill(json({ detail: "nope" }, 401)));
  await page.goto("/login");
  await expect(page.getByRole("link", { name: /Request your archive export/ })).toHaveAttribute("href", "/archive-export");
});
