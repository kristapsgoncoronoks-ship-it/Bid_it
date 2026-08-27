import { test, expect, type Page } from "@playwright/test";

/**
 * WO-X — capturing several invoices in one drop, over the LIVE app shell with
 * the API mocked via `page.route` (deterministic, no backend).
 *
 * The property under test is not "it accepts a list". It is that a batch is
 * PARTIAL: three files arrive together, two are admitted and one is refused,
 * and the screen says so file by file — two review links and one reason —
 * instead of collapsing the drop into a single success or a single failure.
 *
 * The files are DROPPED on the dropzone rather than fed to the input, because
 * dropping a folder's worth of scans is the actual gesture this exists for and
 * it goes through a different handler.
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

const QUOTA_MESSAGE =
  "Monthly upload limit reached (2/2) for your organization's plan. " +
  "Upgrade your plan or ask a platform operator to raise the limit.";

const BATCH_RESULT = {
  accepted: 2,
  rejected: 1,
  outcomes: [
    { filename: "depot-june.csv", accepted: true, extraction_run_id: "run-1" },
    { filename: "depot-july.csv", accepted: true, extraction_run_id: "run-2" },
    {
      filename: "depot-august.csv",
      accepted: false,
      extraction_run_id: null,
      code: "upload_quota_reached",
      message: QUOTA_MESSAGE,
    },
  ],
};

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
      return route.fulfill(json(BATCH_RESULT, 202));
    }
    return route.fulfill(json({}));
  });
}

/** Drop `names` on the dropzone the way a person drops a folder of scans. */
async function dropFiles(page: Page, names: string[]): Promise<void> {
  const data = await page.evaluateHandle((fileNames) => {
    const dt = new DataTransfer();
    for (const name of fileNames) {
      const body = `description,quantity,unit_price,invoice_number\nDiesel,1,10,${name}\n`;
      dt.items.add(new File([body], name, { type: "text/csv" }));
    }
    return dt;
  }, names);
  await page.getByRole("button", { name: /drag it here/i }).dispatchEvent("drop", {
    dataTransfer: data,
  });
}

test("three dropped invoices are captured together, and each one reports its own outcome", async ({
  page,
}) => {
  await mockApi(page);
  await page.goto("/upload");

  await expect(page.getByRole("heading", { level: 1, name: "Upload invoices" })).toBeVisible();

  await dropFiles(page, ["depot-june.csv", "depot-july.csv", "depot-august.csv"]);

  // All three are held before anything is sent — a drop is a selection, not a
  // submission, so a mis-drop can still be removed.
  await expect(page.getByText("depot-june.csv")).toBeVisible();
  await expect(page.getByText("depot-august.csv")).toBeVisible();

  await page.getByRole("button", { name: "Upload 3 files" }).click();

  const results = page.getByRole("region", { name: "Upload results" });
  await expect(results.getByRole("heading", { name: "2 of 3 queued for reading" })).toBeVisible();

  // The two admitted files each carry their own way into the review screen…
  await expect(results.getByRole("link", { name: "Review" })).toHaveCount(2);
  await expect(results.getByRole("link", { name: "Review" }).first()).toHaveAttribute(
    "href",
    "/captures/run-1",
  );
  // …and the refused one states the server's reason rather than failing the drop.
  await expect(results.getByText(QUOTA_MESSAGE)).toBeVisible();

  // Nothing navigated: with several files there is no single place to go.
  await expect(page).toHaveURL(/\/upload$/);
});

test("a single dropped invoice still walks straight into its review screen", async ({ page }) => {
  await mockApi(page);
  await page.route("**/api/v1/invoices/upload/batch", async (route) =>
    route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({
        accepted: 1,
        rejected: 0,
        outcomes: [{ filename: "one.csv", accepted: true, extraction_run_id: "run-9" }],
      }),
    }),
  );
  await page.goto("/upload");

  await dropFiles(page, ["one.csv"]);
  await page.getByRole("button", { name: "Upload", exact: true }).click();

  await expect(page).toHaveURL(/\/captures\/run-9/);
});
