import { test, expect, type Page } from "@playwright/test";

/**
 * Payment Runs: "Cancel" requires an explicit ConfirmDialog before firing
 * (WO-39 / R17), over the LIVE app shell with `page.route` mocking — same
 * deterministic, no-backend pattern as `issue-destructive-actions.spec.ts` /
 * `upload-duplicate.spec.ts`.
 *
 * Prior behaviour called `cancel.mutate(r.id)` directly in the button's
 * `onClick`, firing `DELETE /payment-runs/{id}` on a single click with no
 * chance to back out — inconsistent with this exact page's own ConfirmDialog
 * pattern for re-export / skipped-payee acknowledgement. This spec proves the
 * click now opens a styled confirm dialog first, that cancelling the DIALOG
 * issues NO request, and that confirming calls the delete endpoint exactly
 * once.
 *
 * Synthetic fixtures only (master context §10): fictional names/ids/figures.
 */

const ORG = { id: "org-1", name: "Test Workspace", status: "active" };
const USER = {
  id: "user-1",
  email: "owner@test.io",
  name: "Owner",
  role: "owner",
  org_id: "org-1",
};

const OPEN_RUN = {
  id: "run-open-1",
  reference: null,
  method: "bank_transfer",
  status: "open",
  note: null,
  total_eur: "1200.00",
  paid_at: null,
  created_by: "owner@test.io",
  approved_by: null,
  approved_at: null,
  exported_at: null,
  export_count: 0,
  last_msg_id: null,
  version: 1,
  created_at: "2026-07-20T09:00:00Z",
  invoice_count: 3,
};

async function mockApi(page: Page): Promise<{ calls: string[] }> {
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));
  const calls: string[] = [];

  const json = (body: unknown, status = 200) => ({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

  await page.route("**/api/v1/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname.replace(/^.*\/api\/v1/, "");
    const method = req.method();

    if (path === "/auth/me") return route.fulfill(json({ user: USER, organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/modules") return route.fulfill(json([]));
    if (path === "/payment-runs/payable" && method === "GET") return route.fulfill(json([]));
    if (path === "/payment-runs" && method === "GET") return route.fulfill(json([OPEN_RUN]));
    if (path === `/payment-runs/${OPEN_RUN.id}` && method === "DELETE") {
      calls.push("cancel");
      return route.fulfill({ status: 204, contentType: "application/json", body: "" });
    }

    return route.fulfill(json([]));
  });

  return { calls };
}

test("cancelling a payment run requires an explicit confirm", async ({ page }) => {
  const { calls } = await mockApi(page);
  await page.goto("/payment-runs");

  await expect(page.getByRole("heading", { level: 1, name: "Payment runs" })).toBeVisible();
  await expect(page.getByText(`${OPEN_RUN.invoice_count} invoices`)).toBeVisible();
  // Only one run is seeded, so exactly one row-level "Cancel" action button exists
  // before the dialog opens (the dialog's own "Cancel" dismiss button appears later,
  // scoped to `dialog` below so the two never get confused).
  const cancelButton = page.getByRole("button", { name: "Cancel", exact: true });
  await expect(cancelButton).toHaveCount(1);

  // Clicking "Cancel" opens the confirm dialog — it must NOT fire the request yet.
  await cancelButton.click();
  const dialog = page.getByRole("dialog", { name: /Cancel this payment run/ });
  await expect(dialog).toBeVisible();
  expect(calls).not.toContain("cancel");

  // Cancelling the DIALOG leaves the run unmodified and issues no request.
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(dialog).toBeHidden();
  expect(calls).not.toContain("cancel");

  // Re-open and confirm — exactly one call to the delete endpoint, dialog closes.
  await cancelButton.click();
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Cancel run" }).click();
  await expect(dialog).toBeHidden();
  expect(calls.filter((c) => c === "cancel")).toHaveLength(1);
});
