import { test, expect, type Page } from "@playwright/test";

// UI and failure-state evidence; backend suites verify the actual signed CSV.
const ORG = { id: "org-1", name: "Synthetic Company", status: "active" };
const USER = { id: "user-1", org_id: ORG.id, name: "Owner", role: "owner", email: "owner@example.com" };
const ENTITY = { id: "11111111-1111-4111-8111-111111111111", name: "Entity A", legal_name: "Entity A" };

const FORMATS = [
  { key: "generic", action_id: "ACC01", label: "Generic accounting CSV", enabled: true, contract: "generic-v2", reason: null, setup_steps: [] },
  { key: "xero", action_id: "ACC02", label: "Xero — unconfigured", enabled: false, contract: null, reason: "Xero needs the reviewed synthetic template.", setup_steps: ["Ask your administrator and accountant to review Xero mappings."] },
  { key: "quickbooks", action_id: "ACC03", label: "QuickBooks — unconfigured", enabled: false, contract: null, reason: "QuickBooks needs the reviewed synthetic edition.", setup_steps: ["Ask your administrator and accountant to review QuickBooks mappings."] },
];

async function open(page: Page, failure = false, catalogue: unknown = FORMATS, formatStatus = 200, preparePreview = true) {
  let downloads = 0;
  let scope: Record<string, unknown> | null = null;
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "synthetic-token"));
  const json = (body: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
  await page.route("**/api/v1/**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace("/api/v1", "");
    if (path === "/auth/me") return route.fulfill(json({ user: USER, organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/export/formats") return route.fulfill(json(catalogue, formatStatus));
    if (path === "/modules") return route.fulfill(json([]));
    if (path === "/invoices") return route.fulfill(json({ items: [], total: 0, page: 1, page_size: 20 }));
    if (path === "/export/accounting/entities") return route.fulfill(json([ENTITY]));
    if (path === "/export/accounting/preview") {
      scope = req.postDataJSON();
      return route.fulfill(json({
        snapshot_id: "preview-1", contract: "generic-v2", entity_name: ENTITY.name, scope,
        included: 1, excluded_unapproved: 2, excluded_filters: 3, excluded_other_currencies: 1,
        unassigned_in_period: 4, missing_fx: 0, token: "synthetic-snapshot", net: "100.00", tax: "21.00", gross: "121.00", gross_eur: "121.00",
        rows: [{ invoice_id: "invoice-1", number: "MATCH", supplier: "Synthetic Supplier", net: "100.00", tax: "21.00", gross: "121.00", currency: "EUR" }],
      }));
    }
    if (path === "/export/accounting/download") {
      downloads += 1;
      expect(req.method()).toBe("POST");
      expect(req.postDataJSON()).toEqual({ token: "synthetic-snapshot" });
      if (failure) return route.fulfill(json({ detail: "Export permission changed.", code: "forbidden" }, 403));
      return route.fulfill({ status: 200, contentType: "text/csv", body: "Net,VAT,Gross,Gross EUR\n100.00,21.00,121.00,121.00\n" });
    }
    return route.fulfill(json({ items: [], total: 0 }));
  });
  await page.goto("/invoices?q=MATCH&status=pending&workflow_state=approved");
  if (!preparePreview) return { downloads: () => downloads, scope: () => scope };
  await page.getByRole("button", { name: "Accounting CSV", exact: true }).click();
  await page.getByLabel("Legal entity", { exact: true }).selectOption(ENTITY.id);
  await page.getByLabel("From", { exact: true }).fill("2026-05-01");
  await page.getByLabel("To", { exact: true }).fill("2026-05-31");
  await page.getByRole("button", { name: "Preview approved invoices", exact: true }).click();
  await expect(page.getByText("Included: 1.", { exact: false })).toBeVisible();
  return { downloads: () => downloads, scope: () => scope };
}

test("preview names the entity, period, currency, list filters and exclusions", async ({ page }) => {
  const result = await open(page);
  expect(result.scope()).toEqual({ entity_id: ENTITY.id, date_from: "2026-05-01", date_to: "2026-05-31", currency: "EUR", status: "pending", q: "MATCH", workflow_state: "approved" });
  await expect(page.getByText(/Net 100.00 \/ VAT 21.00 \/ Gross 121.00 EUR/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Xero — unconfigured", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "QuickBooks — unconfigured", exact: true })).toBeDisabled();
  await expect(page.getByText(/Xero and QuickBooks need a reviewed import contract/)).toBeVisible();
});

test("the download uses only the previewed snapshot and starts one file", async ({ page }) => {
  const result = await open(page);
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download previewed CSV", exact: true }).click();
  expect((await download).suggestedFilename()).toBe("invoices-generic-v2.csv");
  expect(result.downloads()).toBe(1);
  await expect(page.getByText(/CSV download started/)).toBeVisible();
});

test("a failed blob download explains its permission and retry path", async ({ page }) => {
  await open(page, true);
  await page.getByRole("button", { name: "Download previewed CSV", exact: true }).click();
  await expect(page.getByText(/Export permission changed.*fresh preview/)).toBeVisible();
});

test("changing the scope discards the preview instead of downloading stale selection", async ({ page }) => {
  await open(page);
  await page.getByLabel("Currency", { exact: true }).fill("USD");
  await expect(page.getByRole("button", { name: "Download previewed CSV", exact: true })).toHaveCount(0);
});


test("each blocked adapter exposes server reason and keyboard setup without exporting", async ({ page }) => {
  const exports: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().includes("/export/accounting")) exports.push(request.url());
  });
  await open(page);
  // The helper prepares a generic preview; help itself must perform no command.
  exports.length = 0;
  await page.getByRole("button", { name: "Accounting CSV", exact: true }).click();
  for (const provider of ["Xero", "QuickBooks"]) {
    const reason = provider === "Xero" ? FORMATS[1].reason! : FORMATS[2].reason!;
    await expect(page.getByRole("button", { name: `${provider} — unconfigured`, exact: true })).toHaveAccessibleDescription(reason);
    await expect(page.getByText(reason, { exact: true })).toBeVisible();
    const help = page.getByText(`${provider} setup requirements`, { exact: true });
    await help.focus();
    await help.press("Enter");
    await expect(page.getByText(`Ask your administrator and accountant to review ${provider} mappings.`)).toBeVisible();
  }
  expect(exports).toEqual([]);
});

for (const status of [403, 429, 500]) {
  test(`catalogue ${status} stays blocked with safe read retry and generic preview intact`, async ({ page }) => {
    await open(page, false, { detail: "PRIVATE_CONFIGURATION", code: "unavailable" }, status);
    await expect(page.getByText(/Provider availability cannot be determined/)).toBeVisible();
    await expect(page.getByText("PRIVATE_CONFIGURATION")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Retry provider availability" })).toBeEnabled();
    await expect(page.getByRole("button", { name: "Download previewed CSV", exact: true })).toBeVisible();
  });
}

for (const [name, catalogue] of [
  ["missing descriptor", FORMATS.slice(0, 2)],
  ["duplicate descriptor", [FORMATS[0], FORMATS[1], FORMATS[1]]],
  ["unsupported enablement", FORMATS.map((item) => item.key === "xero" ? { ...item, enabled: true } : item)],
] as const) {
  test(`${name} fails closed`, async ({ page }) => {
    await open(page, false, catalogue);
    await expect(page.getByText(/Provider availability cannot be determined/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Xero — unconfigured", exact: true })).toHaveCount(0);
  });
}


test("a failed catalogue read can recover without an export command", async ({ page }) => {
  await open(page, false, {}, 500, false);
  await expect(page.getByText(/Provider availability cannot be determined/)).toBeVisible();
  let reads = 0;
  await page.route("**/api/v1/export/formats", (route) => {
    reads += 1;
    return route.fulfill({ contentType: "application/json", body: JSON.stringify(FORMATS) });
  });
  await page.getByRole("button", { name: "Retry provider availability" }).click();
  await expect(page.getByRole("button", { name: "Xero — unconfigured", exact: true })).toBeDisabled();
  expect(reads).toBe(1);
});

test("a transport failure has a bounded, read-only recovery", async ({ page }) => {
  await page.route("**/api/v1/export/formats", (route) => route.abort("timedout"));
  // More specific route is installed last by open; override its fallback.
  await open(page, false, FORMATS, 200, false);
  await page.route("**/api/v1/export/formats", (route) => route.abort("timedout"));
  await page.reload();
  await expect(page.getByText(/Provider availability cannot be determined/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry provider availability" })).toBeEnabled();
});
