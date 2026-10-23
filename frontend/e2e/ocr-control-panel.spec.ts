import { test, expect, type Page, type Route } from "@playwright/test";
import type { FieldProvenance } from "../src/lib/types";

// Synthetic API fixtures exercise the real interface, not the native worker.
const ORG = { id: "ocr-org", name: "Synthetic workspace", status: "active" };
const USER = { id: "ocr-user", org_id: ORG.id, name: "Reviewer", role: "owner", email: "reviewer@example.com" };
const DRAFT = { vendor_name: "Synthetic supplier", invoice_number: "filename-default", issue_date: "2026-10-21", currency: "EUR", source_filename: "synthetic.pdf", line_items: [{ description: "Service", quantity: "1", unit_price: "100", amount: "100", tax_rate: "21", category: "uncategorized" }] };
const FIELDS: FieldProvenance[] = [
  { field: "vendor_name", status: "extracted", value: "Synthetic supplier", reviewed_value: "Reviewed supplier", confidence: "0.55", provider: "pdf-ocr", low_confidence: false },
  { field: "invoice_number", status: "defaulted", value: "filename-default", confidence: null, provider: "pdf-ocr", low_confidence: true },
  { field: "due_date", status: "missing", value: null, confidence: null, provider: "pdf-ocr", low_confidence: true },
  { field: "currency", status: "extracted", value: "EUR", confidence: "0.55", provider: "pdf-ocr", low_confidence: true },
  { field: "description", line_index: 0, status: "extracted", value: "Service", confidence: "0.55", provider: "pdf-ocr", low_confidence: true },
  { field: "quantity", line_index: 0, status: "defaulted", value: "1", confidence: null, provider: "pdf-ocr", low_confidence: false },
];
const json = (data: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(data) });

function reviewSnapshot(fields: FieldProvenance[] = FIELDS, fingerprint = "b".repeat(64)) {
  const draft = structuredClone(DRAFT);
  for (const field of fields) {
    const value = field.reviewed_value ?? field.value;
    const target = field.line_index == null ? draft : draft.line_items[field.line_index];
    if (target) (target as unknown as Record<string, unknown>)[field.field] = value || null;
  }
  return { items: [], source_sha256: "a".repeat(64), attempt_seq: 0, current_draft: draft, review_fingerprint: fingerprint,
    reviewed_fields: fields.filter(field => field.reviewed_value != null).map(({ field, line_index }) => ({ field, line_index })),
    eligible: false, reason: "Synthetic preview unavailable." };
}

async function setup(page: Page, handler: (route: Route, path: string) => Promise<boolean>, role = "owner") {
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "synthetic-token"));
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    if (path === "/auth/me") return route.fulfill(json({ user: { ...USER, role }, organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/modules") return route.fulfill(json([]));
    if (await handler(route, path)) return;
    if (path === "/invoices/captures/run-1/region-reads") return route.fulfill(json(reviewSnapshot()));
    if (path === "/invoices/captures/run-1/pages") return route.fulfill(json({ status: "unsupported", pages: [], message: "Synthetic preview unavailable." }));
    if (path === "/invoices/duplicate-candidates") return route.fulfill(json({ exact: [], cross_supplier: [], scored: [] }));
    if (path.startsWith("/vendors/resolve")) return route.fulfill(json({ outcome: "create", reason: "Synthetic supplier", candidates: [] }));
    return route.fulfill(json([]));
  });
}

function queue(total: number) {
  return { total, items: total ? [{ extraction_run_id: "run-1", method: "ocr", status: "parsed", source_filename: "synthetic.pdf", invoice_number: "INV-1", vendor_name: "Synthetic supplier", warning_count: 1, total_fields: 6, low_confidence_fields: 3, created_at: "2026-10-21T10:00:00Z" }] : [] };
}

function run() {
  return { status: "parsed", extraction_run_id: "run-1", method: "ocr", draft: { draft: DRAFT, warnings: [], fields: [] } };
}

test("control panel refresh reads both server counts and blocks repeated refresh", async ({ page }) => {
  let reviewReads = 0; let failureReads = 0;
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  await setup(page, async (route, path) => {
    if (path === "/invoices/captures/review") {
      reviewReads += 1; if (reviewReads > 1) await held;
      await route.fulfill(json(queue(reviewReads > 1 ? 1 : 3))); return true;
    }
    if (path === "/invoices/captures/failures") {
      failureReads += 1; if (failureReads > 1) await held;
      await route.fulfill(json({ items: [], groups: [], total: failureReads > 1 ? 0 : 2, unacknowledged: failureReads > 1 ? 0 : 2 })); return true;
    }
    return false;
  });
  await page.goto("/captures");
  const status = page.getByRole("region", { name: "Document review status" });
  await expect(status.getByText("3", { exact: true })).toBeVisible();
  await expect(status.getByText("2", { exact: true })).toBeVisible();
  const process = page.getByRole("navigation", { name: "Document processing", exact: true });
  await expect(process.getByRole("link", { name: "Upload document", exact: true })).toHaveAttribute("href", "/upload");
  await expect(process.getByRole("link", { name: "Processing failures", exact: true })).toHaveAttribute("href", "/captures/failures");
  await expect(page.getByRole("table").getByRole("link", { name: "Review →", exact: true })).toHaveAttribute("href", "/captures/run-1");
  await page.getByRole("button", { name: "Refresh status", exact: true }).click();
  await expect(page.getByRole("button", { name: "Refreshing status…", exact: true })).toBeDisabled();
  await expect.poll(() => [reviewReads, failureReads]).toEqual([2, 2]);
  release();
  await expect(status.getByText("1", { exact: true })).toBeVisible();
  await expect(status.getByText("0", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Refresh status", exact: true })).toBeEnabled();
});

test("failed failure-status refresh replaces retained count with unavailable, never all clear", async ({ page }) => {
  let fail = false;
  await setup(page, async (route, path) => {
    if (path === "/invoices/captures/review") { await route.fulfill(json(queue(1))); return true; }
    if (path === "/invoices/captures/failures") { await route.fulfill(fail ? json({ detail: "Synthetic unavailable" }, 503) : json({ items: [], groups: [], total: 2, unacknowledged: 2 })); return true; }
    return false;
  });
  await page.goto("/captures");
  const status = page.getByRole("region", { name: "Document review status" });
  await expect(status.getByText("2", { exact: true })).toBeVisible();
  fail = true;
  await page.getByRole("button", { name: "Refresh status", exact: true }).click();
  await expect(status.getByText("Unavailable", { exact: true })).toBeVisible({ timeout: 15000 });
  await expect(status.getByText("0", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("link", { name: /2 documents arrived/ })).toHaveCount(0);
  await expect(status.getByRole("alert")).toContainText("Couldn’t load failure status");
  fail = false;
  await page.getByRole("button", { name: "Refresh status", exact: true }).click();
  await expect(status.getByText("2", { exact: true })).toBeVisible();
});

test("unavailable review population cannot appear as zero", async ({ page }) => {
  await setup(page, async (route, path) => {
    if (path === "/invoices/captures/review") { await route.fulfill(json({ detail: "Synthetic forbidden" }, 403)); return true; }
    if (path === "/invoices/captures/failures") { await route.fulfill(json({ items: [], groups: [], total: 0, unacknowledged: 0 })); return true; }
    return false;
  });
  await page.goto("/captures");
  const status = page.getByRole("region", { name: "Document review status" });
  await expect(status.getByText("Unavailable", { exact: true })).toBeVisible({ timeout: 15000 });
  await expect(page.getByText("Couldn’t load the capture queue", { exact: true })).toBeVisible();
  await expect(status.getByText("0", { exact: true })).toHaveCount(1);
});

test("review summary separates defaults, missing values and recorded versus unsaved corrections", async ({ page }) => {
  let fields = FIELDS.map((field) => ({ ...field }));
  const writes: unknown[] = [];
  let reviewRevision = 0;
  const fingerprint = () => reviewRevision.toString(16).padStart(64, "0");
  await setup(page, async (route, path) => {
    if (path === "/invoices/captures/run-1/region-reads") { await route.fulfill(json(reviewSnapshot(fields, fingerprint()))); return true; }
    if (path === "/invoices/upload/run-1") { await route.fulfill(json(run())); return true; }
    if (path === "/invoices/captures/run-1/fields") { await route.fulfill(json(fields)); return true; }
    if (path === "/invoices/captures/run-1/review") {
      const body = route.request().postDataJSON() as { expected_review_fingerprint: string; fields: { field: string; reviewed_value: string }[] };
      if (body.expected_review_fingerprint !== fingerprint()) { await route.fulfill(json({ code: "capture_review_changed", detail: "The saved review changed." }, 409)); return true; }
      writes.push(body);
      fields = fields.map((field) => ({ ...field, ...(body.fields.find((f) => f.field === field.field) ?? {}) }));
      reviewRevision++;
      await route.fulfill({ ...json(fields), headers: { "x-capture-review-fingerprint": fingerprint() } }); return true;
    }
    return false;
  });
  await page.goto("/captures/run-1");
  const summary = page.getByRole("region", { name: "Capture review summary" });
  await expect(summary.getByText("Read from source", { exact: true }).locator("..")).toContainText("3");
  await expect(summary.getByText("Filled defaults", { exact: true }).locator("..")).toContainText("2");
  await expect(summary.getByText("Not read", { exact: true }).locator("..")).toContainText("1");
  await expect(summary.getByText("Corrections saved", { exact: true }).locator("..")).toContainText("1");
  await expect(summary).toContainText("not measured recognition accuracy");
  await expect(page.locator("#prov-currency")).toContainText("method-based review score: 55% — please verify");
  await expect(page.getByText("not read from the document — filled with a default", { exact: true })).toBeVisible();
  await page.getByLabel("Invoice number", { exact: true }).fill("CORRECTED-001");
  await expect(summary).toContainText("1 unsaved field edit(s)");
  expect(writes).toHaveLength(0);
  await page.getByRole("button", { name: "Save corrections", exact: true }).click();
  await expect.poll(() => writes.length).toBe(1);
  await expect(summary.getByText("Corrections saved", { exact: true }).locator("..")).toContainText("2");
  await expect(summary.getByText(/unsaved field edit/)).toHaveCount(0);
});

test("unknown provider score is not labelled as the built-in method heuristic", async ({ page }) => {
  const fields: FieldProvenance[] = [
    { ...FIELDS[0], reviewed_value: null, provider: "future-provider", confidence: "0.92" },
    { field: "due_date", status: "extracted", value: "2026-11-21", provider: "future-provider", confidence: null, low_confidence: false },
    { field: "currency", status: "extracted", value: "EUR", provider: "csv", confidence: null, low_confidence: false },
  ];
  await setup(page, async (route, path) => {
    if (path === "/invoices/upload/run-1") { await route.fulfill(json(run())); return true; }
    if (path === "/invoices/captures/run-1/region-reads") { await route.fulfill(json(reviewSnapshot(fields))); return true; }
    if (path === "/invoices/captures/run-1/fields") {
      await route.fulfill(json(fields)); return true;
    }
    return false;
  });
  await page.goto("/captures/run-1");
  await expect(page.getByText("provider score: 92%", { exact: true })).toBeVisible();
  await expect(page.getByText("no provider score supplied", { exact: true })).toBeVisible();
  await expect(page.getByText("structured source — no probabilistic score", { exact: true })).toBeVisible();
  await expect(page.getByText(/The built-in OCR reader uses/)).toHaveCount(0);
});

test("missing provenance displays uncertainty instead of zero review counts", async ({ page }) => {
  await setup(page, async (route, path) => {
    if (path === "/invoices/upload/run-1") { await route.fulfill(json(run())); return true; }
    if (path === "/invoices/captures/run-1/fields") { await route.fulfill(json({ detail: "Synthetic unavailable" }, 503)); return true; }
    return false;
  });
  await page.goto("/captures/run-1");
  const summary = page.getByRole("region", { name: "Capture review summary" });
  await expect(summary.getByRole("alert")).toContainText("Review counts unavailable", { timeout: 15000 });
  await expect(summary.getByText("0", { exact: true })).toHaveCount(0);
});

test("document controls remain available to invoice-reading auditors on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await setup(page, async (route, path) => {
    if (path === "/invoices/captures/review") { await route.fulfill(json(queue(0))); return true; }
    if (path === "/invoices/captures/failures") { await route.fulfill(json({ items: [], groups: [], total: 0, unacknowledged: 0 })); return true; }
    return false;
  }, "auditor");
  await page.goto("/captures");
  const process = page.getByRole("navigation", { name: "Document processing", exact: true });
  await expect(process.getByRole("link", { name: "Upload document", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Refresh status", exact: true })).toBeEnabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
