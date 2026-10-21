import { test, expect, type Page } from "@playwright/test";

const ORG = { id: "region-org", name: "Synthetic OCR Workspace", status: "active" };
const DRAFT = { invoice_number: "OLD-1", vendor_name: "Synthetic Supplier", issue_date: "2026-10-14", due_date: null, currency: "EUR", source_filename: "synthetic-invoice.pdf",
  po_reference: null, supplier_registration_number: null, supplier_vat_id: null, supplier_iban: null,
  line_items: [{ description: "Existing service", quantity: "1", unit_price: "10.00", amount: "10.00", tax_rate: "0", category: "uncategorized", captured_tax_amount: null, captured_gross_amount: null }] };
type Draft = typeof DRAFT;
type Field = { field: string; line_index: number | null; value: string | null; reviewed_value: string | null; status: string; low_confidence: boolean; original_value: string | null; normalized_value: string | null };
type Read = Record<string, unknown> & { id: string; command_key: string; status: string; request: Record<string, unknown>; result: Record<string, unknown> | null };
interface State {
  draft: Draft; fields: Field[]; read: Read | null; posts: Record<string, unknown>[]; applications: Record<string, unknown>[];
  lookups: number; legacyReads: number; failCreate: boolean; createMissing: boolean; failApply: boolean; failReadback: boolean;
  conflict: boolean; readStatus: string; metadataFailure: boolean; eligible: boolean; fingerprint: string; reviewPosts: Record<string, unknown>[]; invoicePosts: Record<string, unknown>[]; omitReviewHeader: boolean; holdReview?: () => Promise<void>; holdCreate?: () => Promise<void>; holdApply?: () => Promise<void>;
}
async function setup(page: Page, opts: { corrected?: boolean; reader?: boolean; snapshotFailure?: boolean } = {}) {
  const fields: Field[] = [
    ...Object.entries(DRAFT).filter(([key]) => !["line_items", "source_filename"].includes(key)).map(([field, value]) => ({ field, line_index: null, value: value == null ? null : String(value), reviewed_value: field === "invoice_number" && opts.corrected ? "HUMAN-7" : null, status: value == null ? "missing" : "extracted", low_confidence: false, original_value: value == null ? null : String(value), normalized_value: value == null ? null : String(value) })),
    ...Object.entries(DRAFT.line_items[0]).map(([field, value]) => ({ field, line_index: 0, value: value == null ? null : String(value), reviewed_value: null, status: value == null ? "missing" : "extracted", low_confidence: false, original_value: value == null ? null : String(value), normalized_value: value == null ? null : String(value) })),
  ];
  const state: State = { draft: structuredClone(DRAFT), fields, read: null, posts: [], applications: [], lookups: 0, legacyReads: 0, failCreate: false, createMissing: false, failApply: false, failReadback: false, conflict: false, readStatus: "ready", metadataFailure: opts.snapshotFailure ?? false, eligible: true, fingerprint: "b".repeat(64), reviewPosts: [], invoicePosts: [], omitReviewHeader: false };
  const effective = () => {
    const draft = structuredClone(state.draft);
    for (const field of state.fields) if (field.reviewed_value != null) {
      const destination = field.line_index == null ? draft : draft.line_items[field.line_index];
      if (destination) (destination as unknown as Record<string, unknown>)[field.field] = field.reviewed_value;
    }
    return draft;
  };
  const reviewed = () => state.fields.filter((field) => field.reviewed_value != null).map(({ field, line_index }) => ({ field, line_index }));
  const output = () => ({ ...state.read, extraction_run_id: "run-1", review_fingerprint: state.fingerprint, current_draft: effective(), reviewed_fields: reviewed(), applied: state.read?.status === "applied", error_code: null, error_detail: null });
  const json = (data: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(data) });
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "synthetic-region-token"));
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request(), path = new URL(request.url()).pathname.replace("/api/v1", ""), method = request.method();
    if (path === "/auth/me") return route.fulfill(json({ user: { id: "region-user", org_id: ORG.id, name: "Synthetic Owner", role: opts.reader ? "auditor" : "owner", email: "owner@example.com" }, organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/modules") return route.fulfill(json([]));
    if (path === "/invoices/upload/run-1") {
      state.legacyReads++;
      if (state.failReadback && state.read?.status === "applied") return route.fulfill(json({ detail: "Saved capture temporarily unavailable" }, 503));
      return route.fulfill(json({ status: "parsed", extraction_run_id: "run-1", method: "ocr", draft: { draft: state.draft, warnings: [], fields: [] } }));
    }
    if (path === "/invoices/captures/run-1/fields") return route.fulfill(json(state.fields));
    if (path === "/invoices/captures/run-1/review") {
      const body = request.postDataJSON(); state.reviewPosts.push(body); await state.holdReview?.();
      if (body.expected_review_fingerprint !== state.fingerprint) return route.fulfill(json({ detail: "Saved review changed", code: "capture_review_changed" }, 409));
      for (const change of body.fields) {
        const field = state.fields.find((field) => field.field === change.field && (field.line_index ?? null) === (change.line_index ?? null));
        if (field) field.reviewed_value = change.reviewed_value;
      }
      state.fingerprint = String.fromCharCode(state.fingerprint.charCodeAt(0) + 1).repeat(64);
      return route.fulfill({ ...json(state.fields), headers: state.omitReviewHeader ? {} : { "x-capture-review-fingerprint": state.fingerprint } });
    }
    if (path === "/invoices/captures/run-1/pages") return route.fulfill(json({ status: "ready", pages: [{ number: 1, width: 400, height: 600, size: 100 }], total_pages: 1, truncated: false }));
    if (path.endsWith("/pages/1/doc")) return route.fulfill({ status: 200, contentType: "image/svg+xml", body: '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="600"><rect width="400" height="600" fill="white"/><text x="40" y="90" font-size="20">Invoice NEW-2026</text><text x="40" y="180" font-size="20">Service 12.50</text></svg>' });
    if (path === "/invoices/captures/run-1/region-reads" && method === "GET") {
      if (state.metadataFailure) return route.fulfill(json({ detail: "Settings unavailable" }, 503));
      return route.fulfill(json({ items: state.read ? [output()] : [], source_sha256: "a".repeat(64), attempt_seq: 0, current_draft: effective(), review_fingerprint: state.fingerprint, reviewed_fields: reviewed(), eligible: state.eligible, reason: state.eligible ? null : "This capture is no longer available for applying changes." }));
    }
    if (path === "/invoices/captures/run-1/region-reads" && method === "POST") {
      const body = request.postDataJSON(); state.posts.push(body); await state.holdCreate?.();
      if (!state.createMissing) state.read = { id: "read-1", command_key: body.command_key, status: state.readStatus, request: body,
        result: state.readStatus === "ready" ? { text: "Invoice NEW-2026\nService A 12.50\nService B 7.50", engine: "Tesseract", warnings: [], fields: body.target === "table" ? [] : [{ field: body.field, line_index: body.line_index ?? null, value: body.field === "vendor_name" ? "Re-read Supplier" : "NEW-2026" }],
          rows: body.target === "table" ? [{ description: "Service A", quantity: "1", unit_price: "12.50", amount: "12.50", tax_rate: "0", category: "uncategorized", captured_tax_amount: null, captured_gross_amount: null }, { description: "Service B", quantity: "1", unit_price: "7.50", amount: "7.50", tax_rate: "0", category: "uncategorized", captured_tax_amount: null, captured_gross_amount: null }] : [] } : null };
      return route.fulfill(state.failCreate ? json({ detail: "Read response unavailable" }, 503) : json(output(), 202));
    }
    if (path.includes("/region-reads/by-command/")) { state.lookups++; return route.fulfill(state.read ? json(output()) : json({ detail: "Command not found" }, 404)); }
    if (path === "/invoices/captures/run-1/region-reads/read-1") return route.fulfill(json(output()));
    if (path.endsWith("/region-reads/read-1/apply")) {
      const body = request.postDataJSON(); state.applications.push(body); await state.holdApply?.();
      if (state.conflict) return route.fulfill(json({ detail: "Saved capture changed" }, 409));
      for (const change of body.fields) {
        const field = state.fields.find((field) => field.field === change.field && (field.line_index ?? null) === (change.line_index ?? null));
        if (field) field.reviewed_value = change.value;
      }
      for (const mapping of body.rows) {
        const candidate = (state.read!.result!.rows as typeof DRAFT.line_items)[mapping.candidate_index];
        const index = mapping.target_line_index ?? state.draft.line_items.length;
        if (mapping.target_line_index == null) state.draft.line_items.push({ ...candidate });
        for (const key of mapping.fields) {
          let field = state.fields.find((field) => field.field === key && field.line_index === index);
          if (!field) { field = { field: key, line_index: index, value: null, reviewed_value: null, status: "missing", low_confidence: false, original_value: null, normalized_value: null }; state.fields.push(field); }
          field.reviewed_value = (candidate as unknown as Record<string, string | null>)[key];
        }
      }
      state.read!.status = "applied"; state.read!.apply_command_key = body.command_key; state.fingerprint = "c".repeat(64);
      state.read!.applied_result = { draft: effective(), review_fingerprint: "c".repeat(64), changed_fields: body.fields.length, appended_rows: body.rows.filter((row: { target_line_index: number | null }) => row.target_line_index == null).length };
      return route.fulfill(state.failApply ? json({ detail: "Apply response unavailable" }, 503) : json(output()));
    }
    if (path === "/invoices" && method === "POST") {
      const body = request.postDataJSON(); state.invoicePosts.push(body);
      if (body.capture_review_fingerprint !== state.fingerprint) return route.fulfill(json({ detail: "Saved review changed", code: "capture_review_changed" }, 409));
      return route.fulfill(json({ ...effective(), id: "created-region", status: "pending", subtotal: "10.00", tax_amount: "0.00", total: "10.00" }, 201));
    }
    if (path === "/invoices/created-region") return route.fulfill(json({ ...effective(), id: "created-region", vendor_id: "synthetic-vendor", status: "pending", subtotal: "10.00", tax_amount: "0.00", total: "10.00", validation_status: "none", validation_findings: [], amount_paid: "0.00", outstanding: "10.00" }));
    if (path === "/invoices/duplicate-candidates") return route.fulfill(json({ exact: [], cross_supplier: [], scored: [] }));
    if (path.startsWith("/vendors/resolve")) return route.fulfill(json({ outcome: "create", reason: "Synthetic supplier", candidates: [] }));
    if (path === "/dashboard" || path.startsWith("/dashboard/")) return route.fulfill(json({ outstanding: 0, counts: {}, overdue: 0 }));
    return route.fulfill(json([]));
  });
  await page.goto("/captures/run-1");
  return state;
}
async function editor(page: Page) {
  await page.getByRole("button", { name: "Select area to re-read", exact: true }).click();
  const panel = page.getByRole("region", { name: "Visual OCR adjustment", exact: true });
  await expect(panel.getByRole("button", { name: "Select whole page", exact: true })).toBeEnabled();
  await expect.poll(() => panel.locator("canvas").evaluate((element: HTMLCanvasElement) => element.width)).toBe(400);
  return panel;
}
async function readWhole(page: Page) {
  const panel = await editor(page); await panel.getByRole("button", { name: "Select whole page", exact: true }).click();
  await panel.getByRole("button", { name: "Re-read selected area", exact: true }).click();
  await expect(panel.getByRole("checkbox", { name: "Apply Invoice number", exact: true })).toBeVisible(); return panel;
}

test("rotated zoomed drag sends normalized original-page coordinates and preserves values until apply", async ({ page }) => {
  const state = await setup(page), panel = await editor(page);
  await panel.getByRole("button", { name: "Rotate page clockwise", exact: true }).click();
  await expect.poll(() => panel.locator("canvas").evaluate((element: HTMLCanvasElement) => element.width)).toBe(600);
  await panel.getByRole("button", { name: "Zoom in", exact: true }).click();
  const bounds = await panel.getByRole("img", { name: "OCR selection area", exact: true }).boundingBox(); expect(bounds).toBeTruthy();
  await page.mouse.move(bounds!.x + bounds!.width * 0.1, bounds!.y + bounds!.height * 0.2); await page.mouse.down();
  await page.mouse.move(bounds!.x + bounds!.width * 0.6, bounds!.y + bounds!.height * 0.5); await page.mouse.up();
  await panel.getByRole("button", { name: "Re-read selected area", exact: true }).click();
  await expect.poll(() => state.posts.length).toBe(1);
  expect(state.posts[0]).toMatchObject({ rotation: 90, page: 1, language: "eng", source_sha256: "a".repeat(64), expected_attempt_seq: 0, target: "field", field: "invoice_number" });
  expect(state.posts[0].x).toBeCloseTo(0.1, 2); expect(state.posts[0].y).toBeCloseTo(0.2, 2); expect(state.posts[0].width).toBeCloseTo(0.5, 2); expect(state.posts[0].height).toBeCloseTo(0.3, 2);
  await expect(page.getByLabel("Invoice number", { exact: true })).toHaveValue("OLD-1"); expect(state.applications).toHaveLength(0);
});

test("keyboard area controls reject bounds and apply only explicitly selected fields", async ({ page }) => {
  const state = await setup(page), panel = await editor(page);
  await panel.getByLabel("Selection left (%)").fill("90"); await panel.getByLabel("Selection width (%)").fill("50");
  await expect(panel.getByRole("button", { name: "Re-read selected area", exact: true })).toBeDisabled();
  await panel.getByRole("button", { name: "Select whole page", exact: true }).click(); await panel.getByRole("button", { name: "Re-read selected area", exact: true }).click();
  const choose = panel.getByRole("checkbox", { name: "Apply Invoice number", exact: true }); await expect(choose).not.toBeChecked();
  await expect(panel.getByRole("button", { name: "Apply selected changes", exact: true })).toBeDisabled();
  await choose.check(); await panel.getByRole("button", { name: "Apply selected changes", exact: true }).click();
  await expect(page.getByLabel("Invoice number", { exact: true })).toHaveValue("NEW-2026");
  await expect(panel.getByText(/Selected changes applied and loaded/)).toBeVisible();
  expect(state.applications).toHaveLength(1); expect(state.applications[0]).toMatchObject({ fields: [{ field: "invoice_number", line_index: null, value: "NEW-2026" }], rows: [], overwrite_reviewed: false });
  await expect(page.getByLabel("Vendor", { exact: true })).toHaveValue("Synthetic Supplier");
});

test("queued read locks competing work and ready read releases it", async ({ page }) => {
  const state = await setup(page); state.readStatus = "queued";
  const panel = await editor(page); await panel.getByRole("button", { name: "Select whole page", exact: true }).click(); await panel.getByRole("button", { name: "Re-read selected area", exact: true }).click();
  await expect(panel.getByText("Queued for the reader… No capture values have changed.", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Invoice number", { exact: true })).toBeDisabled();
  await expect(panel.getByRole("button", { name: "Re-read selected area", exact: true })).toBeDisabled();
  expect(state.posts).toHaveLength(1);
});

test("worker completion updates ready status and history without another read request", async ({ page }) => {
  const state = await setup(page); state.readStatus = "queued";
  const panel = await editor(page); await panel.getByRole("button", { name: "Select whole page", exact: true }).click(); await panel.getByRole("button", { name: "Re-read selected area", exact: true }).click();
  await expect(panel.getByRole("button", { name: "Inspect page 1 · queued", exact: true })).toBeVisible();
  state.read!.status = "ready";
  state.read!.result = { text: "Invoice NEW-2026", engine: "Tesseract", warnings: [], fields: [{ field: "invoice_number", line_index: null, value: "NEW-2026" }], rows: [] };
  await expect(panel.getByText("Reading completed. Review the suggestions below; no capture values have changed.", { exact: true })).toBeVisible();
  await expect(panel.getByRole("button", { name: "Inspect page 1 · ready", exact: true })).toBeVisible();
  await expect(panel.getByRole("button", { name: "Inspect page 1 · queued", exact: true })).toHaveCount(0);
  await expect(page.getByLabel("Invoice number", { exact: true })).toBeEnabled();
  expect(state.posts).toHaveLength(1); expect(state.applications).toHaveLength(0);
});

test("lost read response recovers the same committed command through GET only", async ({ page }) => {
  const state = await setup(page); state.failCreate = true;
  const panel = await editor(page); await panel.getByRole("button", { name: "Select whole page", exact: true }).click(); await panel.getByRole("button", { name: "Re-read selected area", exact: true }).click();
  await expect(panel.getByText(/read outcome is unconfirmed/)).toBeVisible();
  await expect(panel.getByRole("button", { name: "Re-read selected area", exact: true })).toBeDisabled();
  await panel.getByRole("button", { name: "Check re-read outcome", exact: true }).click();
  await expect(panel.getByRole("checkbox", { name: "Apply Invoice number", exact: true })).toBeVisible();
  expect(state.posts).toHaveLength(1); expect(state.lookups).toBe(1);
});

test("a missing read can only replay its frozen original command", async ({ page }) => {
  const state = await setup(page); state.failCreate = true; state.createMissing = true;
  const panel = await editor(page); await panel.getByRole("button", { name: "Select whole page", exact: true }).click(); await panel.getByRole("button", { name: "Re-read selected area", exact: true }).click();
  await panel.getByRole("button", { name: "Check re-read outcome", exact: true }).click();
  await expect(panel.getByRole("button", { name: "Retry original read", exact: true })).toBeVisible();
  const first = structuredClone(state.posts[0]); state.failCreate = false; state.createMissing = false;
  await panel.getByRole("button", { name: "Retry original read", exact: true }).click();
  await expect(panel.getByRole("checkbox", { name: "Apply Invoice number", exact: true })).toBeVisible();
  expect(state.posts).toHaveLength(2); expect(state.posts[1]).toEqual(first);
});

test("saved human correction stays unselected and requires explicit overwrite acknowledgement", async ({ page }) => {
  const state = await setup(page, { corrected: true }), panel = await readWhole(page);
  await expect(page.getByLabel("Invoice number", { exact: true })).toHaveValue("HUMAN-7");
  await expect(panel.getByText("Previously corrected", { exact: true })).toBeVisible();
  await expect(panel.getByRole("checkbox", { name: "Apply Invoice number", exact: true })).not.toBeChecked();
  await panel.getByRole("checkbox", { name: "Apply Invoice number", exact: true }).check();
  await expect(panel.getByRole("button", { name: "Apply selected changes", exact: true })).toBeDisabled();
  await panel.getByLabel("Replace only the previously corrected values I selected", { exact: true }).check();
  await panel.getByRole("button", { name: "Apply selected changes", exact: true }).click();
  await expect(page.getByLabel("Invoice number", { exact: true })).toHaveValue("NEW-2026");
  expect(state.applications[0].overwrite_reviewed).toBe(true);
});

test("unsaved corrections block application without being discarded", async ({ page }) => {
  await setup(page); await page.getByLabel("Vendor", { exact: true }).fill("My unsaved supplier");
  const panel = await readWhole(page); await panel.getByRole("checkbox", { name: "Apply Invoice number", exact: true }).check();
  await expect(panel.getByText(/Save your existing corrections before applying/)).toBeVisible();
  await expect(panel.getByRole("button", { name: "Apply selected changes", exact: true })).toBeDisabled();
  await expect(page.getByLabel("Vendor", { exact: true })).toHaveValue("My unsaved supplier");
});

test("lost apply response discovers committed result without a second write", async ({ page }) => {
  const state = await setup(page), panel = await readWhole(page); state.failApply = true;
  await panel.getByRole("checkbox", { name: "Apply Invoice number", exact: true }).check(); await panel.getByRole("button", { name: "Apply selected changes", exact: true }).click();
  await expect(panel.getByText(/apply outcome is unconfirmed/)).toBeVisible();
  await expect(panel.getByRole("button", { name: "Retry original application", exact: true })).toHaveCount(0);
  await panel.getByRole("button", { name: "Check applied result", exact: true }).click();
  await expect(page.getByLabel("Invoice number", { exact: true })).toHaveValue("NEW-2026");
  expect(state.applications).toHaveLength(1);
});

test("known apply survives a failed readback and recovers without applying again", async ({ page }) => {
  const state = await setup(page), panel = await readWhole(page); state.failReadback = true;
  await panel.getByRole("checkbox", { name: "Apply Invoice number", exact: true }).check(); await panel.getByRole("button", { name: "Apply selected changes", exact: true }).click();
  await expect(panel.getByText(/changes were applied, but the saved capture could not reload/)).toBeVisible({ timeout: 15000 });
  await expect(panel.getByRole("button", { name: "Retry original application", exact: true })).toHaveCount(0);
  state.failReadback = false; await panel.getByRole("button", { name: "Check applied result", exact: true }).click();
  await expect(page.getByLabel("Invoice number", { exact: true })).toHaveValue("NEW-2026"); expect(state.applications).toHaveLength(1);
});

test("table comparison maps existing and new rows without deleting unselected cells", async ({ page }) => {
  const state = await setup(page), panel = await editor(page);
  await panel.getByRole("combobox", { name: "Read as", exact: true }).selectOption("table");
  await panel.getByRole("button", { name: "Select whole page", exact: true }).click(); await panel.getByRole("button", { name: "Re-read selected area", exact: true }).click();
  await panel.getByRole("checkbox", { name: "Apply row 1 Net amount", exact: true }).check();
  await panel.getByRole("checkbox", { name: "Select proposed row 2", exact: true }).check();
  await panel.getByRole("button", { name: "Apply selected changes", exact: true }).click();
  await expect(page.getByLabel("Line 1 Description", { exact: true })).toHaveValue("Existing service");
  await expect(page.getByLabel("Line 1 Amount", { exact: true })).toHaveValue("12.50");
  await expect(page.getByLabel("Line 2 Description", { exact: true })).toHaveValue("Service B");
  expect(state.applications[0]).toMatchObject({ fields: [], rows: [{ candidate_index: 0, target_line_index: 0, fields: ["amount"] }, { candidate_index: 1, target_line_index: null }] });
});

test("stale apply shows GET recovery and preserves the current invoice", async ({ page }) => {
  const state = await setup(page), panel = await readWhole(page); state.conflict = true;
  await panel.getByRole("checkbox", { name: "Apply Invoice number", exact: true }).check(); await panel.getByRole("button", { name: "Apply selected changes", exact: true }).click();
  await expect(panel.getByText("The saved capture changed. Refresh the comparison and choose the changes again.", { exact: true })).toBeVisible();
  await expect(panel.getByRole("button", { name: "Apply selected changes", exact: true })).toBeDisabled();
  await panel.getByRole("button", { name: "Refresh comparison", exact: true }).click();
  await expect(panel.getByRole("checkbox", { name: "Apply Invoice number", exact: true })).not.toBeChecked();
  await expect(page.getByLabel("Invoice number", { exact: true })).toHaveValue("OLD-1"); expect(state.applications).toHaveLength(1);
});

test("saving existing corrections and refreshing comparison preserves them through apply", async ({ page }) => {
  const state = await setup(page); await page.getByLabel("Vendor", { exact: true }).fill("Saved human supplier");
  const panel = await readWhole(page);
  await page.getByRole("button", { name: "Save corrections", exact: true }).click();
  await expect(panel.getByText(/Save your existing corrections before applying/)).toHaveCount(0);
  await panel.getByRole("button", { name: "Refresh comparison", exact: true }).click();
  await panel.getByRole("checkbox", { name: "Apply Invoice number", exact: true }).check();
  await panel.getByRole("button", { name: "Apply selected changes", exact: true }).click();
  await expect(page.getByLabel("Vendor", { exact: true })).toHaveValue("Saved human supplier");
  await expect(page.getByLabel("Invoice number", { exact: true })).toHaveValue("NEW-2026");
  expect(state.applications).toHaveLength(1);
});

test("unavailable controls expose GET recovery without queuing work", async ({ page }) => {
  const state = await setup(page), panel = await editor(page);
  state.metadataFailure = true;
  await panel.getByRole("button", { name: "Refresh re-read controls", exact: true }).click();
  await expect(panel.getByText("Couldn’t load re-read controls", { exact: true })).toBeVisible();
  await panel.getByRole("button", { name: "Select whole page", exact: true }).click();
  await expect(panel.getByRole("button", { name: "Re-read selected area", exact: true })).toBeDisabled();
  state.metadataFailure = false;
  await panel.getByRole("button", { name: "Refresh re-read controls", exact: true }).click();
  await expect(panel.getByRole("button", { name: "Re-read selected area", exact: true })).toBeEnabled();
  expect(state.posts).toHaveLength(0);
});

test("a fresh ineligible capture status disables a previously ready application", async ({ page }) => {
  const state = await setup(page), panel = await readWhole(page);
  await panel.getByRole("checkbox", { name: "Apply Invoice number", exact: true }).check();
  await expect(panel.getByRole("button", { name: "Apply selected changes", exact: true })).toBeEnabled();
  state.eligible = false;
  await panel.getByRole("button", { name: "Refresh re-read controls", exact: true }).click();
  await expect(panel.getByText("This capture is no longer available for applying changes.", { exact: true })).toBeVisible();
  await expect(panel.getByRole("button", { name: "Apply selected changes", exact: true })).toBeDisabled();
  expect(state.applications).toHaveLength(0);
});

test("read-only reviewers keep source access but cannot open OCR or edit values on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const state = await setup(page, { reader: true });
  await expect(page.getByRole("img", { name: "Source page 1", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Download original", exact: true })).toBeEnabled();
  await expect(page.getByRole("button", { name: "Select area to re-read", exact: true })).toHaveCount(0);
  await expect(page.getByLabel("Invoice number", { exact: true })).toBeDisabled();
  expect(state.posts).toHaveLength(0);
});

test("mobile selection and comparison stay within the page width", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await setup(page); const panel = await readWhole(page);
  await expect(panel.getByRole("checkbox", { name: "Apply Invoice number", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await panel.screenshot({ path: test.info().outputPath("region-mobile.png") });
  await page.setViewportSize({ width: 1280, height: 900 });
  await panel.screenshot({ path: test.info().outputPath("region-desktop.png") });
});

test("late create after navigating away cannot restore the previous capture", async ({ page }) => {
  const state = await setup(page), panel = await editor(page);
  let release: () => void = () => {}; const gate = new Promise<void>((resolve) => { release = resolve; }); state.holdCreate = () => gate;
  await panel.getByRole("button", { name: "Select whole page", exact: true }).click(); await panel.getByRole("button", { name: "Re-read selected area", exact: true }).click();
  await expect.poll(() => state.posts.length).toBe(1);
  await expect(page.getByRole("button", { name: "Close visual OCR editor", exact: true })).toBeDisabled();
  await page.getByRole("navigation", { name: "Breadcrumb", exact: true }).getByRole("link", { name: "Captures", exact: true }).click(); release();
  await expect(page).toHaveURL(/\/captures$/);
  await expect(page.getByRole("region", { name: "Visual OCR adjustment", exact: true })).toHaveCount(0);
  await expect(page.getByLabel("Invoice number", { exact: true })).toHaveCount(0); expect(state.applications).toHaveLength(0);
});

test("confirmation auto-save binds its loaded base and uses the exact saved response revision", async ({ page }) => {
  const state = await setup(page);
  await page.getByLabel("Vendor", { exact: true }).fill("Chosen Supplier");
  await page.getByRole("button", { name: "Confirm & create invoice", exact: true }).click();
  await expect(page).toHaveURL(/\/invoices\/created-region$/);
  expect(state.reviewPosts).toHaveLength(1);
  expect(state.reviewPosts[0]).toMatchObject({ expected_review_fingerprint: "b".repeat(64), fields: [{ field: "vendor_name", reviewed_value: "Chosen Supplier" }] });
  expect(state.invoicePosts).toHaveLength(1);
  expect(state.invoicePosts[0]).toMatchObject({ vendor_name: "Chosen Supplier", extraction_run_id: "run-1", capture_review_fingerprint: "c".repeat(64) });
});

test("stale auto-save keeps the local draft and cannot create until explicit saved-review reload", async ({ page }) => {
  const state = await setup(page);
  await page.getByLabel("Vendor", { exact: true }).fill("My local draft");
  state.fields.find((field) => field.field === "vendor_name")!.reviewed_value = "Another saved Supplier";
  state.fingerprint = "c".repeat(64);
  await page.getByRole("button", { name: "Confirm & create invoice", exact: true }).click();
  await expect(page.getByText(/The saved review changed\. Your local draft is still shown/)).toBeVisible();
  expect(state.reviewPosts).toHaveLength(1); expect(state.invoicePosts).toHaveLength(0);
  expect(state.reviewPosts[0].expected_review_fingerprint).toBe("b".repeat(64));
  await expect(page.getByLabel("Vendor", { exact: true })).toHaveValue("My local draft");
  await expect(page.getByRole("button", { name: "Confirm & create invoice", exact: true })).toBeDisabled();
  state.metadataFailure = true;
  await page.getByRole("button", { name: "Reload saved review", exact: true }).click();
  await expect(page.getByText(/Couldn’t reload the saved review\. Your local draft is still shown/)).toBeVisible();
  await expect(page.getByLabel("Vendor", { exact: true })).toHaveValue("My local draft");
  expect(state.reviewPosts).toHaveLength(1); expect(state.invoicePosts).toHaveLength(0);
  state.metadataFailure = false;
  await page.getByRole("button", { name: "Reload saved review", exact: true }).click();
  await expect(page.getByLabel("Vendor", { exact: true })).toHaveValue("Another saved Supplier");
  await page.getByRole("button", { name: "Confirm & create invoice", exact: true }).click();
  await expect(page).toHaveURL(/\/invoices\/created-region$/);
  expect(state.reviewPosts).toHaveLength(1);
  expect(state.invoicePosts[0]).toMatchObject({ vendor_name: "Another saved Supplier", capture_review_fingerprint: "c".repeat(64) });
});

test("a fresh comparison read cannot attach its revision to an older dirty draft", async ({ page }) => {
  const state = await setup(page);
  await page.getByLabel("Vendor", { exact: true }).fill("My unsaved supplier");
  const panel = await editor(page);
  state.fields.find((field) => field.field === "vendor_name")!.reviewed_value = "Remote correction";
  state.fingerprint = "d".repeat(64);
  await panel.getByRole("button", { name: "Refresh re-read controls", exact: true }).click();
  await expect(page.getByLabel("Vendor", { exact: true })).toHaveValue("My unsaved supplier");
  await page.getByRole("button", { name: "Confirm & create invoice", exact: true }).click();
  await expect(page.getByText(/The saved review changed\. Your local draft is still shown/)).toBeVisible();
  expect(state.reviewPosts[0].expected_review_fingerprint).toBe("b".repeat(64));
  expect(state.invoicePosts).toHaveLength(0);
  expect(state.fields.find((field) => field.field === "vendor_name")!.reviewed_value).toBe("Remote correction");
});

test("stale final confirmation posts its original revision and preserves visible values", async ({ page }) => {
  const state = await setup(page);
  await expect(page.getByRole("button", { name: "Confirm & create invoice", exact: true })).toBeEnabled();
  state.fields.find((field) => field.field === "invoice_number")!.reviewed_value = "OTHER-TAB-9";
  state.fingerprint = "c".repeat(64);
  await page.getByRole("button", { name: "Confirm & create invoice", exact: true }).click();
  await expect(page.getByText(/The saved review changed\. Your local draft is still shown/)).toBeVisible();
  expect(state.reviewPosts).toHaveLength(0); expect(state.invoicePosts).toHaveLength(1);
  expect(state.invoicePosts[0]).toMatchObject({ invoice_number: "OLD-1", capture_review_fingerprint: "b".repeat(64) });
  await expect(page.getByLabel("Invoice number", { exact: true })).toHaveValue("OLD-1");
  await page.getByRole("button", { name: "Reload saved review", exact: true }).click();
  await expect(page.getByLabel("Invoice number", { exact: true })).toHaveValue("OTHER-TAB-9");
  await page.getByRole("button", { name: "Confirm & create invoice", exact: true }).click();
  await expect(page).toHaveURL(/\/invoices\/created-region$/);
  expect(state.invoicePosts).toHaveLength(2);
  expect(state.invoicePosts[1]).toMatchObject({ invoice_number: "OTHER-TAB-9", capture_review_fingerprint: "c".repeat(64) });
});

test("accepted corrections without a verifiable saved revision cannot continue to create", async ({ page }) => {
  const state = await setup(page); state.omitReviewHeader = true;
  await page.getByLabel("Vendor", { exact: true }).fill("Accepted Supplier");
  await page.getByRole("button", { name: "Confirm & create invoice", exact: true }).click();
  await expect(page.getByText(/corrections were accepted, but their saved revision could not be verified/i)).toBeVisible();
  expect(state.reviewPosts).toHaveLength(1); expect(state.invoicePosts).toHaveLength(0);
  await expect(page.getByLabel("Vendor", { exact: true })).toHaveValue("Accepted Supplier");
  await expect(page.getByRole("button", { name: "Confirm & create invoice", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Reload saved review", exact: true }).click();
  state.omitReviewHeader = false;
  await page.getByRole("button", { name: "Confirm & create invoice", exact: true }).click();
  await expect(page).toHaveURL(/\/invoices\/created-region$/);
  expect(state.reviewPosts).toHaveLength(1);
  expect(state.invoicePosts[0].capture_review_fingerprint).toBe("c".repeat(64));
});

test("an unavailable saved snapshot blocks writes and GET reload restores the current draft", async ({ page }) => {
  const state = await setup(page, { snapshotFailure: true });
  await expect(page.getByText("Couldn’t load the saved review", { exact: true })).toBeVisible();
  expect(state.reviewPosts).toHaveLength(0); expect(state.invoicePosts).toHaveLength(0);
  await expect(page.getByRole("button", { name: "Confirm & create invoice", exact: true })).toHaveCount(0);
  state.metadataFailure = false;
  await page.getByRole("button", { name: "Reload saved review", exact: true }).click();
  await expect(page.getByLabel("Invoice number", { exact: true })).toHaveValue("OLD-1");
  await expect(page.getByRole("button", { name: "Confirm & create invoice", exact: true })).toBeEnabled();
  expect(state.reviewPosts).toHaveLength(0); expect(state.invoicePosts).toHaveLength(0);
});

test("leaving during correction save prevents a late response from creating an invoice", async ({ page }) => {
  const state = await setup(page);
  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => { release = resolve; }); state.holdReview = () => gate;
  await page.getByLabel("Vendor", { exact: true }).fill("Local pending supplier");
  await page.getByRole("button", { name: "Confirm & create invoice", exact: true }).click();
  await expect.poll(() => state.reviewPosts.length).toBe(1);
  await expect(page.getByLabel("Vendor", { exact: true })).toBeDisabled();
  await page.getByRole("link", { name: "Dashboard", exact: true }).click();
  await expect(page).toHaveURL(/\/$/); release();
  await expect.poll(() => state.fingerprint).toBe("c".repeat(64));
  await expect(page.getByRole("heading", { name: "Today", exact: true })).toBeVisible();
  await expect(page.getByLabel("Vendor", { exact: true })).toHaveCount(0);
  expect(state.invoicePosts).toHaveLength(0);
  await expect(page).toHaveURL(/\/$/);
});

test("a saved review without a selectable preview still supports ordinary corrections and confirmation", async ({ page }) => {
  const state = await setup(page);
  await expect(page.getByRole("button", { name: "Confirm & create invoice", exact: true })).toBeEnabled();
  state.eligible = false;
  await page.reload();
  await page.getByLabel("Vendor", { exact: true }).fill("Manual Supplier");
  await page.getByRole("button", { name: "Confirm & create invoice", exact: true }).click();
  await expect(page).toHaveURL(/\/invoices\/created-region$/);
  expect(state.reviewPosts).toHaveLength(1);
  expect(state.invoicePosts).toHaveLength(1);
  expect(state.invoicePosts[0]).toMatchObject({ vendor_name: "Manual Supplier", capture_review_fingerprint: "c".repeat(64) });
  expect(state.posts).toHaveLength(0); expect(state.applications).toHaveLength(0);
});
