import { test, expect, type Page, type Route } from "@playwright/test";

// Synthetic browser responses prove client presentation and request interlocks.
// Actual persistence/authorization/row locks are covered by the backend API/PG
// reimbursement tests; these mocks do not qualify bank disposition or settlement.
const ORG = { id: "reimbursement-org", name: "Synthetic payout workspace", status: "active" };
const USER = { id: "reimbursement-user", org_id: ORG.id, name: "Synthetic owner", role: "owner", email: "owner@example.com" };
const OPEN_BATCH = {
  id: "reimbursement-batch", reference: null, method: "bank_transfer", status: "open",
  total_eur: "60.00", paid_at: null, created_by: "maker@example.com",
  exported_at: null as string | null, export_count: 0, last_msg_id: null as string | null,
  version: 1, created_at: "2026-10-26T00:00:00Z", report_count: 1,
};
const EXPORTED = { export_count: 1, exported_at: "2026-10-27T08:00:00Z" };
const CONFLICT = {
  code: "batch_exported_cannot_cancel",
  detail: "This batch has a recorded export and cannot be cancelled. Check the bank file’s status with your bank or accountant before arranging another payment.",
};
const json = (body: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
type Batch = typeof OPEN_BATCH;
const detail = (batch: Batch) => ({
  ...batch,
  reports: batch.status === "cancelled" ? [] : [{
    id: "reimbursement-report", title: "Synthetic depot parking", employee_name: "Synthetic payee",
    total: "60.00", currency: "EUR", total_eur: "60.00", status: "marked_for_reimbursement",
    payment_reference: null, reimbursed_at: null,
  }],
});
type Wire = {
  batch: Batch; batchReads: number; detailReads: number; approvedReads: number; deletes: number; exports: string[];
  exportCalls: { path: string; search: string }[]; exportConflict: "already_exported" | "skipped_payees" | null;
  holdCancel: boolean; pendingCancel?: Route; conflict: boolean;
  holdExport: boolean; pendingExport?: Route;
  holdReadback: boolean; pendingReadbacks: Route[]; readbackStatus: number;
};

async function setup(page: Page, patch: Partial<Batch> = {}) {
  const wire: Wire = {
    batch: { ...OPEN_BATCH, ...patch }, batchReads: 0, detailReads: 0, approvedReads: 0, deletes: 0, exports: [], exportCalls: [], exportConflict: null,
    holdCancel: false, conflict: false, holdExport: false, holdReadback: false, pendingReadbacks: [], readbackStatus: 200,
  };
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "synthetic-reimbursement-token"));
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/api/v1", "");
    const method = route.request().method();
    if (path === "/auth/me") return route.fulfill(json({ user: USER, organization: ORG, permissions: ["invoice.read", "expense.read", "expense.approve"] }));
    if (path === "/auth/organizations") return route.fulfill(json([{ org_id: ORG.id, name: ORG.name, role: USER.role, current: true }]));
    if (path === "/modules") return route.fulfill(json([{ key: "expenses", name: "Employee expenses", enabled: true, ready: true, requires_issuer: false }]));
    if (path === "/reimbursements/eligible" && method === "GET") {
      wire.approvedReads += 1;
      return route.fulfill(json({ items: [], total: 0, page: 1, page_size: 100 }));
    }
    if (path === "/reimbursements/page" && method === "GET") {
      wire.batchReads += 1;
      return route.fulfill(json({ items: [wire.batch], total: 1, page: 1, page_size: 100 }));
    }
    if (path === `/reimbursements/${OPEN_BATCH.id}` && method === "GET") {
      wire.batchReads += 1;
      wire.detailReads += 1;
      if (wire.holdReadback) { wire.pendingReadbacks.push(route); return; }
      if (wire.readbackStatus !== 200) return route.fulfill(json({ detail: "Synthetic batch readback unavailable" }, wire.readbackStatus));
      return route.fulfill(json(detail(wire.batch)));
    }
    if (path === `/reimbursements/${OPEN_BATCH.id}` && method === "DELETE") {
      wire.deletes += 1;
      if (wire.holdCancel) { wire.pendingCancel = route; return; }
      if (wire.conflict) {
        wire.batch = { ...wire.batch, ...EXPORTED };
        return route.fulfill(json(CONFLICT, 409));
      }
      wire.batch = { ...wire.batch, status: "cancelled", version: wire.batch.version + 1, report_count: 0 };
      return route.fulfill({ status: 204, body: "" });
    }
    if (path === `/reimbursements/${OPEN_BATCH.id}/export` || path === `/reimbursements/${OPEN_BATCH.id}/sepa`) {
      expect(method).toBe("POST");
      wire.exports.push(path);
      wire.exportCalls.push({ path, search: url.search });
      if (wire.holdExport) { wire.pendingExport = route; return; }
      if (wire.exportConflict === "already_exported" && url.searchParams.get("confirm_reexport") !== "true") {
        wire.batch = { ...wire.batch, ...EXPORTED };
        return route.fulfill(json({ code: "already_exported", detail: "This batch was already exported. Re-exporting produces a NEW bank file with a new message id." }, 409));
      }
      if (wire.exportConflict === "skipped_payees" && url.searchParams.get("acknowledge_skipped") !== "true") {
        return route.fulfill(json({ code: "skipped_payees", detail: "1 payee(s) have no IBAN on file and would be dropped from the bank file: Synthetic skipped payee. Add their bank details, or pass acknowledge_skipped=true to export without them." }, 409));
      }
      wire.batch = { ...wire.batch, ...EXPORTED };
      const sepa = path.endsWith("/sepa");
      return route.fulfill({
        contentType: sepa ? "application/xml" : "text/csv",
        headers: { "Content-Disposition": `attachment; filename="synthetic-reimbursement.${sepa ? "xml" : "csv"}"`, "X-Content-Type-Options": "nosniff", ...(sepa ? { "X-Skipped": "1" } : {}) },
        body: sepa ? '<?xml version="1.0" encoding="UTF-8"?><Document xmlns="urn:iso:std:iso:20022:tech:xsd:pain.001.001.03"><SyntheticFixtureOnly/></Document>' : "employee,amount\nSynthetic payee,60.00\n",
      });
    }
    return route.fulfill(json([]));
  });
  await page.goto("/reimbursements");
  await expect(page.getByRole("heading", { level: 1, name: "Reimbursements", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Cancel", exact: true })).toBeVisible();
  return wire;
}

const cancel = (page: Page) => page.getByRole("button", { name: "Cancel", exact: true });
const confirmation = (page: Page) => page.getByRole("dialog", { name: /Cancel this reimbursement batch/i });
async function confirmCancel(page: Page) {
  await cancel(page).click();
  await expect(confirmation(page)).toBeVisible();
  await confirmation(page).getByRole("button", { name: "Cancel batch", exact: true }).click();
}
async function expectConflictingActionsDisabled(page: Page) {
  for (const name of ["Cancel", "Mark paid", "Export CSV", "Export SEPA"]) {
    await expect(page.getByRole("button", { name, exact: true })).toBeDisabled();
  }
  await expect(page.getByRole("textbox", { name: "Payment reference", exact: true })).toBeDisabled();
}

for (const [name, marker] of [
  ["recorded export count", { export_count: 1 }],
  ["legacy negative count", { export_count: -1 }],
  ["legacy timestamp only", { exported_at: EXPORTED.exported_at }],
  ["legacy message ID only", { last_msg_id: "REIMB-SYNTHETIC-LEGACY" }],
  ["legacy empty nonnull message ID", { last_msg_id: "" }],
] as const) {
  test(`${name} blocks cancellation with an accessible bank-disposition explanation`, async ({ page }) => {
    const wire = await setup(page, marker);
    await expect(cancel(page)).toBeDisabled();
    await expect(cancel(page)).toHaveAccessibleDescription(/bank.*accountant|accountant.*bank/i);
    const description = await cancel(page).getAttribute("aria-describedby");
    expect(description).toBeTruthy();
    const help = page.locator(`[id="${description}"]`);
    await expect(help).toBeVisible();
    await expect(help).toContainText(/status|reconcil/i);
    expect(wire.deletes).toBe(0);
  });
}

test("unexported cancellation explains report release, allows dismissal, then sends one DELETE", async ({ page }) => {
  const wire = await setup(page);
  await expect(cancel(page)).toBeEnabled();
  await cancel(page).click();
  const dialog = confirmation(page);
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText(/unexported/i);
  await expect(dialog).toContainText(/report.*(return|available)|return.*report/i);
  await expect(dialog).not.toContainText("nothing is exported or paid");
  expect(wire.deletes).toBe(0);
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(dialog).toBeHidden();
  expect(wire.deletes).toBe(0);
  await confirmCancel(page);
  await expect.poll(() => wire.deletes).toBe(1);
  await expect.poll(() => wire.batchReads).toBeGreaterThan(1);
  expect(wire.detailReads).toBeGreaterThan(0);
  await expect(page.getByText("cancelled", { exact: true })).toBeVisible();
  await expect(cancel(page)).toHaveCount(0);
  expect(wire.deletes).toBe(1);
});

test("a stale exported conflict refreshes current data and keeps its recovery alert", async ({ page }) => {
  const wire = await setup(page);
  wire.conflict = true;
  await confirmCancel(page);
  await expect.poll(() => wire.deletes).toBe(1);
  await expect.poll(() => wire.batchReads).toBeGreaterThan(1);
  expect(wire.detailReads).toBeGreaterThan(0);
  await expect(cancel(page)).toBeDisabled();
  const alert = page.getByRole("alert").filter({ hasText: "recorded export" });
  await expect(alert).toBeVisible();
  await expect(alert).toContainText("bank or accountant");
  await expect(cancel(page)).toHaveAccessibleDescription(/bank.*accountant/i);
  expect(wire.deletes).toBe(1);
});

test("a failed readback stays locked and its explicit retry only reads current status", async ({ page }) => {
  const wire = await setup(page);
  wire.conflict = true;
  wire.readbackStatus = 500;
  await confirmCancel(page);
  const refresh = page.getByRole("button", { name: "Refresh batch status", exact: true });
  await expect(refresh).toBeVisible();
  await expectConflictingActionsDisabled(page);
  expect(wire.deletes).toBe(1);
  expect(wire.exports).toHaveLength(0);
  const failedReads = wire.batchReads;
  wire.readbackStatus = 200;
  await refresh.click();
  await expect.poll(() => wire.batchReads).toBeGreaterThan(failedReads);
  await expect(refresh).toHaveCount(0);
  await expect(cancel(page)).toBeDisabled();
  await expect(cancel(page)).toHaveAccessibleDescription(/bank.*accountant/i);
  await expect(page.getByRole("alert").filter({ hasText: "recorded export" })).toBeVisible();
  expect(wire.deletes).toBe(1);
  expect(wire.exports).toHaveLength(0);
});

test("a real scoped read timeout offers a GET-only retry without repeating cancellation", async ({ page }) => {
  const wire = await setup(page);
  wire.conflict = true;
  wire.holdReadback = true;
  // This is the real 15s Axios deadline, with no fake clock or timeout mock.
  // The 20s expectation budget belongs to this new bounded-read contract.
  const abortedRead = page.waitForEvent("requestfailed", {
    predicate: (request) => request.method() === "GET" && new URL(request.url()).pathname === `/api/v1/reimbursements/${OPEN_BATCH.id}`,
    timeout: 20_000,
  });
  await confirmCancel(page);
  await expect.poll(() => wire.pendingReadbacks.length).toBe(1);
  const refresh = page.getByRole("button", { name: "Refresh batch status", exact: true });
  await expect(refresh).toBeVisible({ timeout: 20_000 });
  expect((await abortedRead).failure()?.errorText).toMatch(/aborted/i);
  await expectConflictingActionsDisabled(page);
  expect(wire.detailReads).toBe(1);
  expect(wire.deletes).toBe(1);
  expect(wire.exports).toHaveLength(0);

  // The aborted Route is not replayable. A new user-requested GET must own the
  // recovery, and the original cancellation must never be sent a second time.
  wire.pendingReadbacks.splice(0);
  await refresh.click();
  await expect.poll(() => wire.pendingReadbacks.length).toBe(1);
  expect(wire.detailReads).toBe(2);
  wire.holdReadback = false;
  await wire.pendingReadbacks.shift()!.fulfill(json(detail(wire.batch)));
  await expect(cancel(page)).toHaveAccessibleDescription(/bank.*accountant/i);
  await expect(page.getByRole("button", { name: "Export CSV", exact: true })).toBeEnabled();
  await expect(refresh).toHaveCount(0);
  await expect(page.getByRole("alert").filter({ hasText: "recorded export" })).toBeVisible();
  expect(wire.deletes).toBe(1);
  expect(wire.exports).toHaveLength(0);
});

test("a pending export blocks competing actions until exported readback arrives", async ({ page }) => {
  const wire = await setup(page);
  wire.holdExport = true;
  wire.holdReadback = true;
  await page.getByRole("button", { name: "Export CSV", exact: true }).click();
  await expect.poll(() => wire.pendingExport !== undefined).toBe(true);
  await expectConflictingActionsDisabled(page);
  expect(wire.deletes).toBe(0);
  wire.batch = { ...wire.batch, ...EXPORTED };
  await wire.pendingExport!.fulfill({ contentType: "text/csv", body: "employee,amount\nSynthetic payee,60.00\n" });
  await expect.poll(() => wire.pendingReadbacks.length).toBeGreaterThan(0);
  await expectConflictingActionsDisabled(page);
  wire.holdReadback = false;
  for (const route of wire.pendingReadbacks.splice(0)) await route.fulfill(json(detail(wire.batch)));
  await expect(cancel(page)).toBeDisabled();
  await expect(cancel(page)).toHaveAccessibleDescription(/bank.*accountant/i);
  await expect(page.getByRole("button", { name: "Export CSV", exact: true })).toBeEnabled();
  await expect.poll(() => wire.approvedReads).toBeGreaterThan(1);
  expect(wire.exports).toHaveLength(1);
  expect(wire.deletes).toBe(0);
});

test("a pending cancellation blocks payment and exports until cancelled readback arrives", async ({ page }) => {
  const wire = await setup(page);
  wire.holdCancel = true;
  wire.holdReadback = true;
  await confirmCancel(page);
  await expect.poll(() => wire.pendingCancel !== undefined).toBe(true);
  await expectConflictingActionsDisabled(page);
  wire.batch = { ...wire.batch, status: "cancelled", version: 2, report_count: 0 };
  await wire.pendingCancel!.fulfill({ status: 204, body: "" });
  await expect.poll(() => wire.pendingReadbacks.length).toBeGreaterThan(0);
  await expectConflictingActionsDisabled(page);
  wire.holdReadback = false;
  for (const route of wire.pendingReadbacks.splice(0)) await route.fulfill(json(detail(wire.batch)));
  await expect(page.getByText("cancelled", { exact: true })).toBeVisible();
  await expect(cancel(page)).toHaveCount(0);
  await expect.poll(() => wire.approvedReads).toBeGreaterThan(1);
  expect(wire.deletes).toBe(1);
  expect(wire.exports).toHaveLength(0);
});

for (const scenario of [
  { code: "already_exported", button: "Export CSV", path: "export", title: "Re-export bank file?", confirm: "Re-export with a new message id", flag: "confirm_reexport", extension: "csv" },
  { code: "skipped_payees", button: "Export SEPA", path: "sepa", title: "Some employees will not be paid", confirm: "I understand these will not be paid", flag: "acknowledge_skipped", extension: "xml" },
] as const) {
  test(`${scenario.code} warning waits for readback then confirms one flagged export`, async ({ page }) => {
    const wire = await setup(page);
    wire.exportConflict = scenario.code;
    wire.holdReadback = true;
    await page.getByRole("button", { name: scenario.button, exact: true }).click();
    await expect.poll(() => wire.pendingReadbacks.length).toBe(1);
    const warning = page.getByRole("dialog", { name: scenario.title, exact: true });
    await expect(warning).toHaveCount(0);
    await expectConflictingActionsDisabled(page);
    expect(wire.exportCalls).toEqual([{ path: `/reimbursements/${OPEN_BATCH.id}/${scenario.path}`, search: "" }]);
    expect(wire.deletes).toBe(0);

    // Publishing this fresh detail releases the row before the existing warning
    // can be confirmed; confirmation must never disappear into a busy no-op.
    expect(wire.batch.id).toBe(OPEN_BATCH.id);
    wire.holdReadback = false;
    await wire.pendingReadbacks.shift()!.fulfill(json(detail(wire.batch)));
    await expect(warning).toBeVisible();
    await expect(warning).toContainText(scenario.code === "already_exported" ? "new file with a new message id" : "Synthetic skipped payee");
    const download = page.waitForEvent("download");
    await warning.getByRole("button", { name: scenario.confirm, exact: true }).click();
    await expect(warning).toBeHidden();
    await expect.poll(() => wire.exportCalls.length).toBe(2);
    expect(wire.exportCalls).toEqual([
      { path: `/reimbursements/${OPEN_BATCH.id}/${scenario.path}`, search: "" },
      { path: `/reimbursements/${OPEN_BATCH.id}/${scenario.path}`, search: `?${scenario.flag}=true` },
    ]);
    expect((await download).suggestedFilename()).toBe(`reimbursement-${OPEN_BATCH.id}.${scenario.extension}`);
    await expect.poll(() => wire.detailReads).toBe(2);
    await expect(page.getByRole("button", { name: scenario.button, exact: true })).toBeEnabled();
    await expect(cancel(page)).toBeDisabled();
    expect(wire.deletes).toBe(0);
    expect(wire.exportCalls).toHaveLength(2);
  });
}

test("the blocked batch content stays within its card at 390px with 32px root text", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => document.addEventListener("DOMContentLoaded", () => { document.documentElement.style.fontSize = "32px"; }));
  await setup(page, EXPORTED);
  await expect(cancel(page)).toBeDisabled();
  const description = await cancel(page).getAttribute("aria-describedby");
  expect(description).toBeTruthy();
  const help = page.locator(`[id="${description}"]`);
  await expect(help).toBeVisible();
  // Keep this cancellation regression scoped to its payout card. Creation has
  // a separate full-page enlarged-text containment check; no device/a11y certification.
  const bounds = await help.evaluate((element) => {
    const row = element.parentElement!;
    const rect = row.getBoundingClientRect();
    return { width: rect.width, scrollWidth: row.scrollWidth, clientWidth: row.clientWidth, helpRight: element.getBoundingClientRect().right, rowRight: rect.right };
  });
  expect(bounds.scrollWidth).toBeLessThanOrEqual(bounds.clientWidth + 1);
  expect(bounds.helpRight).toBeLessThanOrEqual(bounds.rowRight + 1);
  expect(bounds.width).toBeLessThanOrEqual(390);
});
