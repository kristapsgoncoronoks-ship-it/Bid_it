import { test, expect, type Page, type Route } from "@playwright/test";

/**
 * Job photos (WO-F) over the LIVE app shell, API mocked via `page.route`.
 *
 * What earns its place here:
 *  - the Photos card renders thumbnails from the authenticated download
 *    route (no second serving surface), and "+ Add photo" uploads with
 *    kind=photo;
 *  - photos stay OUT of the documents table (they live in the grid);
 *  - copy is industry-neutral (owner guard list).
 *
 * Synthetic fixtures only.
 */

const ORG = { id: "org-1", name: "Test Workspace", status: "active" };

// A 1×1 transparent PNG, base64 — real enough for an <img>.
const PNG_B64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==";

const DOCS = [
  {
    id: "doc-contract",
    kind: "contract",
    filename: "signed-contract.pdf",
    content_type: "application/pdf",
    uploaded_by: "someone@test.io",
    created_at: "2026-08-20T10:00:00+00:00",
  },
  {
    id: "doc-photo-1",
    kind: "photo",
    filename: "before.jpg",
    content_type: "image/png",
    uploaded_by: "someone@test.io",
    created_at: "2026-08-21T10:00:00+00:00",
  },
];

async function open(page: Page, opts: { onUpload?: (kind: string | null) => void } = {}) {
  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));

  const json = (body: unknown, code = 200) => ({
    status: code,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

  await page.route("**/api/v1/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const p = url.pathname.replace(/^.*\/api\/v1/, "");
    const method = route.request().method();

    if (p === "/auth/me")
      return route.fulfill(
        json({
          user: {
            id: "user-1",
            email: "someone@test.io",
            name: "Test User",
            role: "owner",
            org_id: "org-1",
            is_platform_admin: false,
          },
          organization: ORG,
        }),
      );
    if (p === "/auth/organizations") return route.fulfill(json([ORG]));
    if (p === "/modules") return route.fulfill(json([]));

    if (p === "/masters/projects/proj-1/documents") {
      if (method === "POST") {
        opts.onUpload?.(url.searchParams.get("kind"));
        return route.fulfill(json({ ...DOCS[1], id: "doc-photo-new" }, 201));
      }
      return route.fulfill(json(DOCS));
    }
    if (p.startsWith("/masters/projects/proj-1/documents/") && p.endsWith("/download"))
      return route.fulfill({
        status: 200,
        contentType: "image/png",
        body: Buffer.from(PNG_B64, "base64"),
      });
    if (p === "/masters/projects/proj-1/cost-entries") return route.fulfill(json([]));
    if (p === "/masters/projects/proj-1/offers") return route.fulfill(json([]));
    if (p === "/masters/projects/proj-1/invoicing-plan")
      return route.fulfill(
        json({ rows: [], contracted: "0.00", issued: "0.00", remaining: "0.00" }),
      );
    if (p === "/masters/projects/proj-1/pnl")
      return route.fulfill(
        json({
          project_id: "proj-1",
          code: "JOB-7",
          name: "Won contract",
          status: "active",
          basis: "net_eur_live",
          revenue: "0.00",
          credited: "0.00",
          costs: "0.00",
          invoice_costs: "0.00",
          expense_costs: "0.00",
          manual_costs: "0.00",
          profit: "0.00",
          margin_pct: null,
          pnl_frozen_at: null,
          adjustments: [],
          accepted_at: null,
          accepted_by: null,
          acceptance_document_id: null,
          acceptance_note: null,
        }),
      );
    if (p === "/masters/projects")
      return route.fulfill(
        json([
          {
            id: "proj-1",
            code: "JOB-7",
            name: "Won contract",
            status: "active",
            version: 1,
            customer_id: null,
          },
        ]),
      );
    if (p === "/customers") return route.fulfill(json([]));

    return route.fulfill(json({ items: [], total: 0 }));
  });

  await page.goto("/projects/proj-1");
}

test("the photos card shows the grid and uploads with kind=photo", async ({ page }) => {
  const uploads: (string | null)[] = [];
  await open(page, { onUpload: (k) => uploads.push(k) });

  await expect(page.getByRole("heading", { name: "Job photos" })).toBeVisible();
  await expect(page.getByRole("img", { name: "before.jpg" })).toBeVisible();

  await page
    .locator('input[type="file"][accept="image/*"]')
    .setInputFiles({ name: "after.png", mimeType: "image/png", buffer: Buffer.from(PNG_B64, "base64") });
  await expect.poll(() => uploads.length).toBe(1);
  expect(uploads[0]).toBe("photo");
});

test("photos stay out of the documents table", async ({ page }) => {
  await open(page);
  await expect(page.getByRole("heading", { name: "Job photos" })).toBeVisible();
  const docsCard = page.locator(".card", { hasText: "Contract & documents" });
  await expect(docsCard.getByText("signed-contract.pdf")).toBeVisible();
  await expect(docsCard.getByText("before.jpg")).toHaveCount(0);
});

test("the photos copy is industry-neutral", async ({ page }) => {
  await open(page);
  await expect(page.getByRole("heading", { name: "Job photos" })).toBeVisible();
  const text = (await page.locator("body").innerText()).toLowerCase();
  for (const word of ["cargo", "fuel", "vehicle", "driver", "truck", "site crew"]) {
    expect(text).not.toContain(word);
  }
});
