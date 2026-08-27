import { test, expect, type Page } from "@playwright/test";

/**
 * Hash-based re-upload detection (WO-19 / E1.3) over the LIVE app shell, API
 * mocked via `page.route` — deterministic, no backend.
 *
 * WO-X moved this from a request-level error to a per-file OUTCOME. The server
 * answers 202 with one row per file, and a byte-identical re-upload comes back
 * as `accepted: false, code: "duplicate_upload"` carrying the advisory
 * sentence. The screen shows that row with an "Upload anyway" button beside it,
 * which re-sends THAT file alone with `override=true` — so waiving the advisory
 * stays a per-file decision even when nine other invoices were in the drop.
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

    if (path === "/invoices/upload/batch" && req.method() === "POST") {
      if (url.searchParams.get("override") === "true") {
        return route.fulfill(
          json(
            {
              accepted: 1,
              rejected: 0,
              outcomes: [{ filename: "invoice.csv", accepted: true, extraction_run_id: "run-1" }],
            },
            202,
          ),
        );
      }
      return route.fulfill(
        json(
          {
            accepted: 0,
            rejected: 1,
            outcomes: [
              {
                filename: "invoice.csv",
                accepted: false,
                extraction_run_id: null,
                code: "duplicate_upload",
                message: DUPLICATE_MESSAGE,
              },
            ],
          },
          202,
        ),
      );
    }

    // Every other call (the /captures/:id review screen's own data fetches) —
    // this spec only asserts the Upload page's refuse→override→navigate loop,
    // not CaptureReview's rendering.
    return route.fulfill(json({}));
  });
}

async function chooseFile(page: Page): Promise<void> {
  await page.locator('input[type="file"]').setInputFiles({
    name: "invoice.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("description,quantity,unit_price,invoice_number\nFuel,1,10,INV-1\n"),
  });
  await page.getByRole("button", { name: "Upload", exact: true }).click();
}

test("a duplicate is reported on its own row; uploading anyway re-sends it with override", async ({
  page,
}) => {
  await mockApi(page);
  await page.goto("/upload");

  await expect(page.getByRole("heading", { level: 1, name: "Upload invoices" })).toBeVisible();
  await chooseFile(page);

  // The advisory is the SERVER's exact sentence, shown against the file it is
  // about — and nothing navigated, because nothing was captured.
  const results = page.getByRole("region", { name: "Upload results" });
  await expect(results.getByText(DUPLICATE_MESSAGE)).toBeVisible();
  await expect(page).toHaveURL(/\/upload$/);

  await results.getByRole("button", { name: "Upload anyway" }).click();

  // Re-sending that one file with override=true is accepted, and a single
  // accepted file walks into its review screen.
  await expect(page).toHaveURL(/\/captures\/run-1/);
});

test("a refused file leaves the uploader on the page, ready for the next drop", async ({ page }) => {
  await mockApi(page);
  await page.goto("/upload");

  await chooseFile(page);

  const results = page.getByRole("region", { name: "Upload results" });
  await expect(results.getByText(DUPLICATE_MESSAGE)).toBeVisible();
  await expect(page).toHaveURL(/\/upload$/);

  // The dropzone is live again: choosing another file clears the previous
  // result rather than stacking a second verdict on top of the first.
  await page.locator('input[type="file"]').setInputFiles({
    name: "another.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("description,quantity,unit_price,invoice_number\nFuel,1,11,INV-2\n"),
  });
  await expect(results).toBeHidden();
});
