import { test, expect, type Page } from "@playwright/test";

const ORG = { id: "n4-org", name: "Synthetic Workspace", status: "active" };
const USER = { id: "n4-user", org_id: ORG.id, name: "Owner", role: "owner", email: "owner@example.com" };
const DRAFT = { invoice_number: "N4-1", vendor_name: "Synthetic Supplier", issue_date: "2026-10-14", currency: "EUR", source_filename: "source.pdf", line_items: [{ description: "Service", quantity: "1", unit_price: "100", amount: "100", tax_rate: "21", category: "uncategorized" }] };
const JPEG = Buffer.from("/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAAKAAoDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD3+iiigD//2Q==", "base64");

async function setup(page: Page, mode: "ready" | "missing" | "unsupported" | "download-fail" = "ready") {
  const images: number[] = []; let originals = 0;
  const json = (data: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(data) });
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "synthetic-token"));
  await page.route("**/api/v1/**", async (route) => {
    const req = route.request(); const path = new URL(req.url()).pathname.replace("/api/v1", "");
    if (path === "/auth/me") return route.fulfill(json({ user: USER, organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([{ org_id: ORG.id, name: ORG.name, current: true }]));
    if (path === "/modules") return route.fulfill(json([]));
    if (path === "/invoices/upload/run-1") return route.fulfill(json({ status: "parsed", extraction_run_id: "run-1", method: "text-layer", draft: { draft: DRAFT, warnings: [], fields: [] } }));
    if (path === "/invoices/captures/run-1/region-reads") return route.fulfill(json({
      items: [], source_sha256: "a".repeat(64), attempt_seq: 0, current_draft: DRAFT, review_fingerprint: "b".repeat(64), reviewed_fields: [],
      eligible: mode !== "unsupported", reason: mode === "unsupported" ? "This file has no selectable page preview." : null,
    }));
    if (path === "/invoices/captures/run-1/fields") return route.fulfill(json([]));
    if (path === "/invoices/captures/run-1/pages") return route.fulfill(json(mode === "unsupported" ? { status: "unsupported", pages: [], message: "This file has no page images; use the original document." } : { status: "ready", pages: [{ number: 1 }, { number: 2 }], total_pages: 8, truncated: true }));
    if (path.includes("/pages/") && path.endsWith("/doc")) {
      images.push(Number(path.split("/").at(-2)));
      return mode === "missing" ? route.fulfill(json({ detail: "Stored page preview missing" }, 404)) : route.fulfill({ status: 200, contentType: "image/jpeg", body: JPEG });
    }
    if (path === "/invoices/captures/run-1/source") {
      originals += 1;
      return mode === "download-fail" ? route.fulfill(json({ detail: "Permission denied" }, 403)) : route.fulfill({ status: 200, contentType: "application/pdf", body: "%PDF-1.4\nSynthetic source", headers: { "Content-Disposition": "attachment; filename=source.pdf" } });
    }
    if (path === "/invoices/duplicate-candidates") return route.fulfill(json({ exact: [], cross_supplier: [], scored: [] }));
    if (path.startsWith("/vendors/resolve")) return route.fulfill(json({ outcome: "create", reason: "Synthetic supplier", candidates: [] }));
    return route.fulfill(json([]));
  });
  await page.goto("/captures/run-1");
  return { images, originals: () => originals };
}

test("page navigation fetches only selected previews and disables boundaries", async ({ page }) => {
  const state = await setup(page);
  await expect(page.getByRole("img", { name: "Source page 1", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Previous page", exact: true })).toBeDisabled();
  await expect(page.getByText("Previews show the first 2 of 8 pages. Download the original for remaining pages.")).toBeVisible();
  await expect.poll(() => page.getByRole("img", { name: "Source page 1", exact: true }).evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);
  expect(state.originals()).toBe(0); expect(state.images).toEqual([1]);
  await page.getByRole("button", { name: "Next page", exact: true }).click();
  await expect(page.getByRole("img", { name: "Source page 2", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Next page", exact: true })).toBeDisabled();
  expect(state.images).toEqual([1, 2]);
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download original", exact: true }).click();
  expect((await download).suggestedFilename()).toBe("source.pdf");
  expect(state.originals()).toBe(1);
});

test("a missing stored page explains retry and retains the original download", async ({ page }) => {
  const state = await setup(page, "missing");
  await expect(page.getByRole("alert")).toContainText("Page preview unavailable");
  await page.getByRole("button", { name: "Retry page", exact: true }).click();
  await expect.poll(() => state.images.length).toBe(2);
  await expect(page.getByRole("button", { name: "Download original", exact: true })).toBeEnabled();
});

test("download failure shows a visible recovery message", async ({ page }) => {
  await setup(page, "download-fail");
  await page.getByRole("button", { name: "Download original", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Permission denied");
  await expect(page.getByRole("button", { name: "Download original", exact: true })).toBeEnabled();
});

test("unsupported captures retain the original source viewer", async ({ page }) => {
  const state = await setup(page, "unsupported");
  await expect(page.getByText("This file has no page images; use the original document.")).toBeVisible();
  expect(state.originals()).toBe(0);
  await page.getByRole("button", { name: "Load original viewer", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Original document", exact: true })).toBeVisible();
  await expect.poll(() => state.originals()).toBe(1);
  expect(state.images).toHaveLength(0);
});
