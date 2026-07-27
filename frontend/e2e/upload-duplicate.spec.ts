import { test, expect, type Page } from "@playwright/test";

/**
 * Hash-based re-upload detection (WO-19 / E1.3) over the LIVE app shell, API
 * mocked via `page.route` — deterministic, no backend. The upload endpoint
 * (`POST /invoices/upload`) blocks a byte-identical re-upload with
 * `409 {"detail": "...", "code": "duplicate_upload"}` unless the caller
 * explicitly passes `override=true`; the Upload page surfaces this as a
 * `ConfirmDialog` ("Upload anyway") rather than a dead-end error banner.
 *
 * Synthetic fixtures only (master context §10): fictional figures/ids.
 */

const ORG = { id: "org-1", name: "Test Workspace", status: "active" };
const USER = {
  id: "user-1",
  email: "owner@test.io",
  name: "Owner",
  role: "owner",
  org_id: "org-1",
};

const DUPLICATE_MESSAGE =
  "You already uploaded this file on 2026-07-20 and it's still pending review. " +
  "Upload it again if this is intentional.";

async function mockApi(page: Page): Promise<void> {
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));

  const json = (body: unknown, status = 200) => ({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

  await page.route("**/api/v1/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname.replace(/^.*\/api\/v1/, "");

    if (path === "/auth/me") return route.fulfill(json({ user: USER, organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/modules") return route.fulfill(json([]));

    if (path === "/invoices/upload" && req.method() === "POST") {
      if (url.searchParams.get("override") === "true") {
        return route.fulfill(json({ extraction_run_id: "run-1", status: "queued" }, 202));
      }
      return route.fulfill(json({ detail: DUPLICATE_MESSAGE, code: "duplicate_upload" }, 409));
    }

    // Every other call (the /captures/:id review screen's own data fetches) —
    // this spec only asserts the Upload page's confirm→override→navigate loop,
    // not CaptureReview's rendering.
    return route.fulfill(json({}));
  });
}

test("a duplicate upload surfaces a confirm dialog; confirming retries with override and navigates", async ({
  page,
}) => {
  await mockApi(page);
  await page.goto("/upload");

  await expect(page.getByRole("heading", { level: 1, name: "Upload an invoice" })).toBeVisible();

  const fileInput = page.locator('input[type="file"]');
  await fileInput.setInputFiles({
    name: "invoice.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("description,quantity,unit_price,invoice_number\nFuel,1,10,INV-1\n"),
  });

  // First upload is a genuine duplicate per the mock — the confirm dialog
  // appears with the server's exact advisory message, and we do NOT navigate.
  await expect(page.getByRole("heading", { name: "You already uploaded this file" })).toBeVisible();
  await expect(page.getByText(DUPLICATE_MESSAGE)).toBeVisible();
  await expect(page).toHaveURL(/\/upload$/);

  await page.getByRole("button", { name: "Upload anyway" }).click();

  // Confirming retries the SAME file with override=true, which the mock accepts.
  await expect(page).toHaveURL(/\/captures\/run-1/);
});

test("cancelling the duplicate dialog leaves the uploader on the page, unblocked", async ({
  page,
}) => {
  await mockApi(page);
  await page.goto("/upload");

  const fileInput = page.locator('input[type="file"]');
  await fileInput.setInputFiles({
    name: "invoice.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("description,quantity,unit_price,invoice_number\nFuel,1,10,INV-1\n"),
  });

  await expect(page.getByRole("heading", { name: "You already uploaded this file" })).toBeVisible();
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByRole("heading", { name: "You already uploaded this file" })).toBeHidden();
  await expect(page).toHaveURL(/\/upload$/);
});
